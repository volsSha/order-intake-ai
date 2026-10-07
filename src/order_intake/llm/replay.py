"""Record/replay wrapper model: every model request is recorded, replayed or simulated, and logged.

A replay file lives at replay/<namespace>/<request_id>/stepNN-<key16>.json. The key hashes a
provider-independent description of the request, so a changed prompt or input is a visible
REPLAY_MISSING, never a stale answer, and a recording replays under either provider.
"""

import base64
import hashlib
import json
import os
import tempfile
import time
import uuid
from pathlib import Path

from pydantic import TypeAdapter
from pydantic_ai.exceptions import ModelAPIError
from pydantic_ai.messages import ModelMessage, ModelMessagesTypeAdapter, ModelResponse, ToolCallPart
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.wrapper import WrapperModel
from pydantic_ai.settings import ModelSettings

from ..config import Settings
from ..storage import Store, now
from .providers import LiveModel, build_model

SIMULATIONS = ("model_unavailable", "invalid_output")
FORMAT_VERSION = 2
RESPONSE = TypeAdapter(ModelResponse)
MESSAGE_VOLATILE = {"timestamp", "run_id", "conversation_id", "provider_response_id", "provider_details", "usage",
                    "model_name", "provider_name", "provider_url", "finish_reason", "metadata", "state",
                    "workspace_ref"}
PART_VOLATILE = {"timestamp", "id", "provider_name", "provider_details"}


class LLMError(Exception):
    code = "MODEL_UNAVAILABLE"


class ReplayMissing(LLMError):
    code = "REPLAY_MISSING"


def _hash_binary(item):
    if not (isinstance(item, dict) and item.get("kind") == "binary"):
        return item
    data = base64.urlsafe_b64decode(item["data"] + "=" * (-len(item["data"]) % 4))
    return {**item, "data": "sha256:" + hashlib.sha256(data).hexdigest(), "identifier": None}


def _scrub(messages: list[dict]) -> list[dict]:
    ids: dict[str, str] = {}
    out = []
    for message in messages:
        message = {k: v for k, v in message.items() if k not in MESSAGE_VOLATILE}
        parts = []
        for part in message.get("parts", []):
            part = {k: v for k, v in part.items() if k not in PART_VOLATILE}
            if isinstance(part.get("tool_call_id"), str):
                part["tool_call_id"] = ids.setdefault(part["tool_call_id"], f"call-{len(ids) + 1}")
            if isinstance(part.get("content"), list):
                part["content"] = [_hash_binary(c) for c in part["content"]]
            parts.append(part)
        out.append({**message, "parts": parts})
    return out


def canonical_request(messages: list[ModelMessage], params: ModelRequestParameters, settings: Settings) -> dict:
    """Provider-independent description of one model request; its hash is the replay key."""
    tools = [{"name": t.name, "description": t.description, "parameters": t.parameters_json_schema,
              "strict": t.strict} for t in [*params.function_tools, *params.output_tools]]
    return {
        "model": settings.model_id,
        "messages": _scrub(ModelMessagesTypeAdapter.dump_python(messages, mode="json")),
        "tools": tools,
        "output_mode": params.output_mode,
        "max_completion_tokens": settings.max_completion_tokens,
        "seed": settings.seed,
        "reasoning_effort": settings.reasoning_effort,
    }


def replay_key(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def normalized_usage(response: ModelResponse) -> dict:
    u = response.usage
    cost = (response.provider_details or {}).get("cost")
    return {"input_tokens": u.input_tokens, "output_tokens": u.output_tokens,
            "total_tokens": u.input_tokens + u.output_tokens, "cost": cost}


def display_path(path: Path, root: Path) -> str:
    return str(path.relative_to(root)) if path.is_relative_to(root) else str(path)


def _write_atomic(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


class ReplayModel(WrapperModel):
    """One instance per run: it numbers the steps and writes one llm_calls row per model request."""

    def __init__(self, live: LiveModel, settings: Settings, store: Store, request_id: str, *,
                 namespace: str = "extract", simulate: str | None = None, replay_files: list[Path] | None = None,
                 key_extras: dict | None = None):
        super().__init__(live.model)
        self.live = live
        self.config = settings
        self.store = store
        self.request_id = request_id
        self.namespace = namespace
        self.simulate = simulate
        self.step = 0
        self.call_ids: list[str] = []
        self.replay_files = replay_files if replay_files is not None else []
        self.key_extras = key_extras or {}  # judge: its own model id and settings, rubric version, sample index

    def replay_path(self, step: int, key: str) -> Path:
        return self.config.replay_dir / self.namespace / self.request_id / f"step{step:02d}-{key[:16]}.json"

    def _log(self, record: dict) -> None:
        self.store.add_llm_call(record)
        self.call_ids.append(record["call_id"])

    async def request(self, messages: list[ModelMessage], model_settings: ModelSettings | None,
                      model_request_parameters: ModelRequestParameters) -> ModelResponse:
        self.step += 1
        step = self.step
        record = {"call_id": f"{self.request_id}-s{step}-{uuid.uuid4().hex[:8]}", "request_id": self.request_id,
                  "step": step, "model": self.key_extras.get("model", self.config.model_id)}

        if self.simulate == "model_unavailable":
            self._log({**record, "source": "simulated", "error": "simulated: model unavailable"})
            raise LLMError("Simulated failure: model unavailable (no API call made).")
        if self.simulate == "invalid_output":
            self._log({**record, "source": "simulated"})
            name = model_request_parameters.output_tools[0].name
            return ModelResponse(parts=[ToolCallPart(name, '{"lines": "not-a-list"')], model_name="simulated")

        payload = {**canonical_request(messages, model_request_parameters, self.config), **self.key_extras}
        key = replay_key(payload)
        path = self.replay_path(step, key)
        shown = display_path(path, self.config.root)
        mode = self.config.llm_mode
        if mode != "live" and path.exists():
            saved = json.loads(path.read_text(encoding="utf-8"))
            response = RESPONSE.validate_python(saved["response"])
            self.replay_files.append(path)
            self._log({**record, "source": "replay", "replay_file": shown, "provider": saved.get("provider"),
                       "latency_ms": saved.get("latency_ms"), "usage": normalized_usage(response)})
            return response
        if mode == "replay":
            self._log({**record, "source": "replay", "replay_file": shown,
                       "error": f"no recorded response for key {key}"})
            raise ReplayMissing(f"No recorded response at {shown} (key {key}); record one with a live run "
                                "(--mode live).")
        if self.live.provider is None:
            self._log({**record, "source": "live", "error": self.live.problem})
            prefix = "No recorded response for this request and no live model: " if mode == "auto" else ""
            raise LLMError(prefix + str(self.live.problem))

        started = time.monotonic()
        try:
            response = await self.wrapped.request(messages, model_settings, model_request_parameters)
        except ModelAPIError as exc:
            self._log({**record, "source": "live", "provider": self.live.provider,
                       "error": f"{type(exc).__name__}: {exc}"})
            raise LLMError(f"Model call failed: {type(exc).__name__}: {exc}") from exc
        latency = int((time.monotonic() - started) * 1000)
        _write_atomic(path, {
            "format_version": FORMAT_VERSION, "replay_key": key, "request_id": self.request_id, "step": step,
            "recorded_at": now(), "provider": self.live.provider, "provider_model": response.model_name,
            "latency_ms": latency, "request": payload, "response": RESPONSE.dump_python(response, mode="json"),
        })
        self._log({**record, "source": "live", "provider": self.live.provider, "replay_file": shown,
                   "latency_ms": latency, "usage": normalized_usage(response)})
        return response


class ModelFactory:
    """Builds one ReplayModel per run; collects the replay files read across runs."""

    def __init__(self, simulate: dict[str, str] | None = None, namespace: str = "extract"):
        self.simulate = simulate if simulate is not None else {}
        self.namespace = namespace
        self.replay_files: list[Path] = []

    def live_model(self, settings: Settings) -> LiveModel:
        return build_model(settings)

    def __call__(self, settings: Settings, store: Store, request_id: str) -> ReplayModel:
        return ReplayModel(self.live_model(settings), settings, store, request_id, namespace=self.namespace,
                           simulate=self.simulate.get(request_id), replay_files=self.replay_files)
