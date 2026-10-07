"""Chat-completions client for OpenRouter with record/replay and labelled simulated failures.

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

from .config import Settings
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

    def _params(self, messages: list[dict], tools: list[dict]) -> dict:
        return {
            "model": self.settings.model_id,
            "messages": messages,
            "tools": tools,
            "tool_choice": "required",
            "max_completion_tokens": self.settings.max_completion_tokens,
            "seed": self.settings.seed,
            "extra_body": {"reasoning": {"effort": self.settings.reasoning_effort}},
        }

    def _key_payload(self, params: dict) -> dict:
        return {**params, "messages": _redact_images(params["messages"])}

    def replay_path(self, request_id: str, step: int, key: str) -> Path:
        return self.settings.replay_dir / request_id / f"step{step:02d}-{key[:16]}.json"

    def complete(self, request_id: str, step: int, messages: list[dict], tools: list[dict]) -> LLMResult:
        params = self._params(messages, tools)
        payload = self._key_payload(params)
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
                raise ReplayMissing(f"No recorded response at {path.relative_to(self.settings.root)}; "
                                    "run with LLM_MODE=live to record one.", record)
            saved = json.loads(path.read_text(encoding="utf-8"))
            record.update(source="replay", replay_file=str(path.relative_to(self.settings.root)),
                          latency_ms=saved.get("latency_ms"), usage=saved["response"].get("usage"))
            return LLMResult(self._message(saved["response"], record), record)
        return self._live(params, payload, path, record)

    def _live(self, params: dict, payload: dict, path: Path, record: dict) -> LLMResult:
        if not self.settings.api_key:
            record.update(source="live", error="OPENROUTER_API_KEY is not set")
            raise LLMError("OPENROUTER_API_KEY is not set; use LLM_MODE=replay or add a key to .env.", record)
        if self._client is None:
            self._client = openai.OpenAI(api_key=self.settings.api_key, base_url=self.settings.base_url,
                                         timeout=self.settings.timeout_s, max_retries=1,
                                         default_headers={"X-Title": "order-intake take-home"})
        started = time.monotonic()
        try:
            response = self._client.chat.completions.create(**params).model_dump(mode="json")
        except openai.OpenAIError as exc:
            record.update(source="live", error=f"{type(exc).__name__}: {exc}")
            raise LLMError(f"Model call failed: {type(exc).__name__}: {exc}", record) from exc
        latency = int((time.monotonic() - started) * 1000)
        record.update(source="live", latency_ms=latency, usage=response.get("usage"),
                      replay_file=str(path.relative_to(self.settings.root)))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "replay_key": record["replay_key"], "request_id": record["request_id"], "step": record["step"],
            "recorded_at": now(), "latency_ms": latency, "request": payload, "response": response,
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
