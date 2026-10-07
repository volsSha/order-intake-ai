"""Chat-completions client (OpenRouter, or OpenAI directly as a fallback) with record/replay.

Every live call is written to replay/<request_id>/step<NN>-<key>.json. The key hashes the full
request (model, messages, tools, parameters), so replay returns exactly the response that the
same conversation produced live, and a changed prompt or input is a replay miss, not a stale hit.
"""

import copy
import hashlib
import json
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

import openai

from .config import ConfigError, Provider, Settings
from .storage import now

SIMULATIONS = ("model_unavailable", "invalid_output")


class LLMError(Exception):
    code = "MODEL_UNAVAILABLE"


class ReplayMissing(LLMError):
    code = "REPLAY_MISSING"


@dataclass
class LLMResult:
    message: dict
    record: dict


def _redact_images(messages: list[dict]) -> list[dict]:
    """Replace inline image data with its hash so keys and replay files stay small and stable."""
    out = copy.deepcopy(messages)
    for m in out:
        if isinstance(m.get("content"), list):
            for part in m["content"]:
                if part.get("type") == "image_url":
                    url = part["image_url"]["url"]
                    part["image_url"]["url"] = "sha256:" + hashlib.sha256(url.encode()).hexdigest()
    return out


class ChatClient:
    def __init__(self, settings: Settings, simulate: dict[str, str] | None = None):
        self.settings = settings
        self.simulate = simulate or {}
        self._client = None
        self._provider: str | None = None

    def _canonical(self, messages: list[dict], tools: list[dict]) -> dict:
        """Provider-independent request description; its hash is the replay key."""
        return {
            "model": self.settings.model_id,
            "messages": _redact_images(messages),
            "tools": tools,
            "tool_choice": "required",
            "max_completion_tokens": self.settings.max_completion_tokens,
            "seed": self.settings.seed,
            "reasoning_effort": self.settings.reasoning_effort,
        }

    def live_params(self, provider: Provider, messages: list[dict], tools: list[dict]) -> dict:
        params = {
            "model": provider.model,
            "messages": messages,
            "tools": tools,
            "tool_choice": "required",
            "max_completion_tokens": self.settings.max_completion_tokens,
            "seed": self.settings.seed,
        }
        if provider.name == "openrouter":
            params["extra_body"] = {"reasoning": {"effort": self.settings.reasoning_effort}}
        else:
            # reasoning_details is an OpenRouter extension the OpenAI API does not accept
            params["messages"] = [{k: v for k, v in m.items() if k != "reasoning_details"} for m in messages]
            params["reasoning_effort"] = self.settings.reasoning_effort
        return params

    def _display(self, path: Path) -> str:
        return str(path.relative_to(self.settings.root)) if path.is_relative_to(self.settings.root) else str(path)

    def replay_path(self, request_id: str, step: int, key: str) -> Path:
        return self.settings.replay_dir / request_id / f"step{step:02d}-{key[:16]}.json"

    def complete(self, request_id: str, step: int, messages: list[dict], tools: list[dict]) -> LLMResult:
        payload = self._canonical(messages, tools)
        key = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        record = {"call_id": f"{request_id}-s{step}-{uuid.uuid4().hex[:8]}", "request_id": request_id,
                  "step": step, "model": self.settings.model_id, "replay_key": key}

        simulation = self.simulate.get(request_id)
        if simulation == "model_unavailable":
            record.update(source="simulated", error="simulated: model unavailable")
            raise LLMError("Simulated failure: model unavailable (no API call made).", record)
        if simulation == "invalid_output":
            record.update(source="simulated")
            message = {"role": "assistant", "content": None, "tool_calls": [{
                "id": "sim-1", "type": "function",
                "function": {"name": "submit_order_draft", "arguments": '{"lines": "not-a-list"'}}]}
            return LLMResult(message, record)

        path = self.replay_path(request_id, step, key)
        mode = self.settings.llm_mode
        if mode == "replay" or (mode == "auto" and path.exists()):
            if not path.exists():
                record.update(source="replay", error="no recorded response for this exact request")
                raise ReplayMissing(f"No recorded response at {self._display(path)}; "
                                    "run with LLM_MODE=live to record one.", record)
            saved = json.loads(path.read_text(encoding="utf-8"))
            record.update(source="replay", replay_file=self._display(path),
                          provider=saved.get("provider"), latency_ms=saved.get("latency_ms"),
                          usage=saved["response"].get("usage"))
            return LLMResult(self._message(saved["response"], record), record)
        return self._live(messages, tools, payload, path, record)

    def _live(self, messages: list[dict], tools: list[dict], payload: dict, path: Path, record: dict) -> LLMResult:
        try:
            provider = self.settings.resolve_provider()
        except ConfigError as exc:
            record.update(source="live", error=str(exc))
            raise LLMError(str(exc), record) from exc
        if provider is None or not provider.api_key:
            record.update(source="live", error="no API key")
            raise LLMError("No API key: set OPENROUTER_API_KEY (or OPENAI_API_KEY as a fallback) in .env, "
                           "or use LLM_MODE=replay.", record)
        record["provider"] = provider.name
        if self._client is None or self._provider != provider.name:
            headers = {"X-Title": "order-intake take-home"} if provider.name == "openrouter" else None
            self._client = openai.OpenAI(api_key=provider.api_key, base_url=provider.base_url,
                                         timeout=self.settings.timeout_s, max_retries=1, default_headers=headers)
            self._provider = provider.name
        params = self.live_params(provider, messages, tools)
        started = time.monotonic()
        try:
            response = self._client.chat.completions.create(**params).model_dump(mode="json")
        except openai.OpenAIError as exc:
            record.update(source="live", error=f"{type(exc).__name__}: {exc}")
            raise LLMError(f"Model call failed: {type(exc).__name__}: {exc}", record) from exc
        latency = int((time.monotonic() - started) * 1000)
        record.update(source="live", latency_ms=latency, usage=response.get("usage"),
                      replay_file=self._display(path))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "replay_key": record["replay_key"], "request_id": record["request_id"], "step": record["step"],
            "recorded_at": now(), "provider": provider.name, "provider_model": provider.model,
            "latency_ms": latency, "request": payload, "response": response,
        }, indent=2), encoding="utf-8")
        return LLMResult(self._message(response, record), record)

    @staticmethod
    def _message(response: dict, record: dict) -> dict:
        choices = response.get("choices") or []
        if response.get("error") or not choices or not choices[0].get("message"):
            record["error"] = f"empty or error response: {response.get('error')}"
            raise LLMError(f"Model returned no message: {response.get('error')}", record)
        msg = choices[0]["message"]
        out = {"role": "assistant", "content": msg.get("content")}
        if msg.get("tool_calls"):
            out["tool_calls"] = [{"id": tc["id"], "type": "function",
                                  "function": {"name": tc["function"]["name"],
                                               "arguments": tc["function"]["arguments"]}}
                                 for tc in msg["tool_calls"]]
        if msg.get("reasoning_details"):
            out["reasoning_details"] = msg["reasoning_details"]
        return out
