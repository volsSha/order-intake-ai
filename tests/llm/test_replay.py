import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime

import pytest
from pydantic_ai.messages import (
    BinaryContent,
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.usage import RequestUsage

from order_intake.config import ROOT
from order_intake.llm.replay import (
    FORMAT_VERSION,
    ModelFactory,
    canonical_request,
    display_path,
    normalized_usage,
    replay_key,
)
from order_intake.pipeline import Pipeline
from order_intake.storage import Store
from tests.conftest import ScriptedModels

PARAMS = ModelRequestParameters(output_mode="tool")


def conversation(stamp: datetime, call_id: str, prompt="Order 2 CAB-1", image: bytes | None = None) -> list:
    content = [prompt, BinaryContent(data=image, media_type="image/png")] if image else prompt
    return [
        ModelRequest(parts=[UserPromptPart(content, timestamp=stamp)], instructions="rules", timestamp=stamp,
                     run_id=f"run-{stamp.second}"),
        ModelResponse(parts=[ToolCallPart("search_catalog", {"query": "CAB-1"}, tool_call_id=call_id)],
                      timestamp=stamp, model_name=f"model-{stamp.second}", provider_name="openrouter",
                      provider_response_id=f"gen-{stamp.second}", usage=RequestUsage(input_tokens=stamp.second)),
        ModelRequest(parts=[ToolReturnPart("search_catalog", {"results": []}, tool_call_id=call_id, timestamp=stamp)]),
    ]


def key(settings, messages) -> str:
    return replay_key(canonical_request(messages, PARAMS, settings))


@pytest.mark.unit
def test_key_ignores_timestamps_run_ids_and_tool_call_ids(settings):
    a = conversation(datetime(2026, 1, 1, 10, 0, 1, tzinfo=UTC), "call_abc")
    b = conversation(datetime(2026, 2, 2, 11, 0, 2, tzinfo=UTC), "toolu_xyz")
    assert key(settings, a) == key(settings, b)
    assert key(settings, a) != key(settings, conversation(datetime(2026, 1, 1, tzinfo=UTC), "c", prompt="Order 3"))


@pytest.mark.unit
def test_key_uses_settings_not_the_provider(settings):
    msgs = conversation(datetime(2026, 1, 1, tzinfo=UTC), "c")
    via_openrouter = replace(settings, openrouter_api_key="or-key")
    via_openai = replace(settings, openai_api_key="oa-key")
    assert key(via_openrouter, msgs) == key(via_openai, msgs)
    assert key(settings, msgs) != key(replace(settings, reasoning_effort="high"), msgs)
    assert key(settings, msgs) != key(replace(settings, model_id="openai/gpt-6-sol"), msgs)


@pytest.mark.unit
def test_image_request_keys_on_the_image_hash(settings):
    stamp = datetime(2026, 1, 1, tzinfo=UTC)
    png = (ROOT / "data" / "requests" / "attachments" / "R11-order-form.png").read_bytes()
    payload = canonical_request(conversation(stamp, "c", image=png), PARAMS, settings)
    image = payload["messages"][0]["parts"][0]["content"][1]
    assert image["data"] == "sha256:" + hashlib.sha256(png).hexdigest()
    assert len(json.dumps(payload)) < 2000
    assert key(settings, conversation(stamp, "c", image=png)) != key(settings, conversation(stamp, "c", image=b"x"))


@pytest.mark.unit
def test_usage_is_normalised_with_cost_when_reported():
    response = ModelResponse(parts=[], usage=RequestUsage(input_tokens=100, output_tokens=20),
                             provider_details={"cost": 0.0012})
    assert normalized_usage(response) == {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120,
                                          "cost": 0.0012}
    assert normalized_usage(ModelResponse(parts=[]))["cost"] is None


@pytest.mark.unit
def test_replay_dir_outside_the_repository_displays_without_error(tmp_path):
    assert display_path(ROOT / "replay" / "extract" / "R1" / "a.json", ROOT) == "replay/extract/R1/a.json"
    assert display_path(tmp_path / "a.json", ROOT) == str(tmp_path / "a.json")


def record(settings, only=frozenset({"R1", "R2", "R11"})):
    """Process requests with the scripted model; ScriptedModels always records like live mode."""
    store = Store(settings.db_path)
    Pipeline(settings, store, model_factory=ScriptedModels()).process_all(only=set(only))
    store.close()


def replay(settings, db_name: str, **overrides):
    s = replace(settings, db_path=settings.db_path.parent / db_name, **overrides)
    store = Store(s.db_path)
    factory = ModelFactory()
    Pipeline(s, store, model_factory=factory).process_all(only={"R1", "R2", "R11"})
    return store, factory


@pytest.mark.integration
def test_recording_has_the_file_format_and_replays_without_a_key(settings):
    record(settings)
    files = sorted((settings.replay_dir / "extract").rglob("*.json"))
    assert [f.relative_to(settings.replay_dir).parts[:2] for f in files][:2] == [("extract", "R1")] * 2
    assert not list(settings.replay_dir.rglob(".tmp-*"))
    body = json.loads(files[0].read_text())
    assert body["format_version"] == FORMAT_VERSION and body["provider"] == "scripted"
    assert {"replay_key", "request_id", "step", "recorded_at", "provider_model", "latency_ms", "request",
            "response"} <= body.keys()
    assert files[0].name == f"step01-{body['replay_key'][:16]}.json"

    store, factory = replay(settings, "replayed.db")
    statuses = {r["request_id"]: r["status"] for r in store.list_requests()}
    assert statuses == {"R1": "ready_for_review", "R2": "needs_clarification", "R11": "ready_for_review"}
    calls = store.llm_calls("R1")
    assert [(c["source"], c["provider"]) for c in calls] == [("replay", "scripted")] * 2
    assert calls[0]["replay_file"] == str(files[0]) and calls[0]["usage"]["total_tokens"] > 0
    assert sorted(factory.replay_files) == files


@pytest.mark.integration
def test_same_request_gets_the_same_keys_across_runs(settings, tmp_path):
    record(settings, only={"R1"})
    first = sorted(p.name for p in settings.replay_dir.rglob("*.json"))
    again = replace(settings, db_path=tmp_path / "again.db", replay_dir=tmp_path / "again")
    record(again, only={"R1"})
    assert sorted(p.name for p in again.replay_dir.rglob("*.json")) == first


@pytest.mark.integration
@pytest.mark.parametrize("keys", [{"openrouter_api_key": "or-key"}, {"openai_api_key": "oa-key"}],
                         ids=["openrouter", "openai-only"])
def test_recording_replays_under_either_provider(settings, keys):
    record(settings)
    store, _ = replay(settings, "other.db", **keys)
    assert all(c["source"] == "replay" and not c["error"] for c in store.llm_calls("R1"))
    assert store.get_request("R1")["status"] == "ready_for_review"


@pytest.mark.integration
def test_changed_prompt_is_replay_missing(settings, tmp_path):
    record(settings, only={"R1"})
    prompt = tmp_path / "prompt.md"
    prompt.write_text(settings.prompt_path.read_text() + "\nOne more rule.\n")
    store, _ = replay(settings, "changed.db", prompt_path=prompt)
    req = store.get_request("R1")
    assert req["status"] == "failed" and req["status_reason"].startswith("REPLAY_MISSING")


@pytest.mark.integration
def test_replay_miss_at_step_two_keeps_step_one_and_records_the_key(settings):
    record(settings, only={"R1"})
    step2 = next(settings.replay_dir.rglob("step02-*.json"))
    step2.unlink()
    store, _ = replay(settings, "miss.db")
    req = store.get_request("R1")
    assert req["status"] == "failed" and req["status_reason"].startswith("REPLAY_MISSING")
    assert store.latest_proposal("R1") is None
    first, miss = store.llm_calls("R1")
    assert (first["step"], first["source"], first["error"]) == (1, "replay", None)
    assert (miss["step"], miss["source"]) == (2, "replay")
    assert step2.name.removesuffix(".json").split("-")[1] in miss["error"]
    assert "no recorded response for key" in miss["error"]


@pytest.mark.integration
@pytest.mark.parametrize("content", ["{not json", '{"format_version": 2}', '{"response": {"parts": 5}}'],
                         ids=["truncated", "no-response", "bad-response"])
def test_unreadable_recording_is_replay_corrupt_not_a_crash(settings, content):
    record(settings, only={"R1"})
    next(settings.replay_dir.rglob("step01-*.json")).write_text(content)
    store, _ = replay(settings, "corrupt.db")
    req = store.get_request("R1")
    assert req["status"] == "failed" and req["status_reason"].startswith("REPLAY_CORRUPT")
    assert store.llm_calls("R1")[0]["error"].startswith("unreadable")
