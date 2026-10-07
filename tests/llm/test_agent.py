from dataclasses import replace

import pytest
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from order_intake.domain.catalog import Catalog
from order_intake.domain.inbox import parse_request_file
from order_intake.llm.agent import ExtractionFailed, extract
from order_intake.llm.providers import LiveModel
from order_intake.llm.replay import ModelFactory, ReplayModel
from order_intake.storage import Store
from tests.conftest import SCRIPTED

pytestmark = pytest.mark.unit

GOOD = {"lines": SCRIPTED["R1"], "clarification_draft": None, "notes": ""}
BROKEN = '{"lines": "not-a-list"'


def search(query="CAB-1 cables"):
    return ToolCallPart("search_catalog", {"query": query})


def submit(info, args=None):
    return ToolCallPart(info.output_tools[0].name, GOOD if args is None else args)


class Scripted:
    """Answers request N with replies[N-1]; the last reply repeats."""

    def __init__(self, *replies):
        self.replies = replies
        self.calls = 0

    def __call__(self, messages, info):
        self.calls += 1
        reply = self.replies[min(self.calls, len(self.replies)) - 1]
        return reply(info) if callable(reply) else reply


@pytest.fixture
def env(settings):
    store = Store(settings.db_path)
    req = parse_request_file(settings.requests_dir / "R1.txt", settings.root)
    catalog = Catalog.load(settings.catalog_path)

    def run(fn, *, simulate=None, provider="scripted"):
        model = ReplayModel(LiveModel(FunctionModel(fn), provider), replace(settings, llm_mode="live"), store, "R1",
                            simulate=simulate)
        return extract(req, catalog, model, settings, store)

    yield store, run
    store.close()


def test_search_then_submit_returns_the_draft_and_lookups(env):
    store, run = env
    out = run(Scripted(ModelResponse(parts=[search()]), lambda info: ModelResponse(parts=[submit(info)])))
    assert out.submission.lines[0].sku == "CAB-1"
    assert out.looked_up_skus == {"CAB-1"}
    calls = store.llm_calls("R1")
    assert [c["call_id"] for c in calls] == out.llm_call_ids and len(calls) == 2
    tools = store.tool_calls("R1")
    assert [(t["step"], t["name"], t["llm_call_id"]) for t in tools] == [
        (1, "search_catalog", out.llm_call_ids[0]), (2, "submit_order_draft", out.llm_call_ids[1])]
    assert tools[0]["result"]["results"][0]["sku"] == "CAB-1"
    assert tools[1]["arguments"] == GOOD


@pytest.mark.parametrize("order", ["search_first", "submit_first"])
def test_search_in_the_same_response_as_submit_still_counts(env, order):
    store, run = env

    def both(info):
        parts = [search(), submit(info)]
        return ModelResponse(parts=parts if order == "search_first" else parts[::-1])

    out = run(Scripted(both))
    assert out.looked_up_skus == {"CAB-1"}
    assert len(store.llm_calls("R1")) == 1


def test_sku_comes_only_from_what_the_catalog_returned(env):
    _, run = env
    out = run(Scripted(ModelResponse(parts=[search("HUB-1 please"), search("moon adapter")]),
                       lambda info: ModelResponse(parts=[submit(info)])))
    assert out.looked_up_skus == {"HUB-1"}


def test_invalid_submit_twice_is_invalid_model_output_with_two_calls(env):
    store, run = env
    fn = Scripted(lambda info: ModelResponse(parts=[submit(info, BROKEN)]))
    with pytest.raises(ExtractionFailed) as exc:
        run(fn)
    assert exc.value.code == "INVALID_MODEL_OUTPUT"
    assert fn.calls == 2 and len(store.llm_calls("R1")) == 2


def test_one_repair_retry_is_enough_to_recover(env):
    _, run = env
    out = run(Scripted(ModelResponse(parts=[search()]), lambda info: ModelResponse(parts=[submit(info, BROKEN)]),
                       lambda info: ModelResponse(parts=[submit(info)])))
    assert out.submission.lines[0].quantity == 2 and len(out.llm_call_ids) == 3


def test_simulated_invalid_output_fails_after_one_retry(env):
    store, run = env
    fn = Scripted(ModelResponse(parts=[search()]))
    with pytest.raises(ExtractionFailed) as exc:
        run(fn, simulate="invalid_output")
    assert exc.value.code == "INVALID_MODEL_OUTPUT" and fn.calls == 0
    assert [c["source"] for c in store.llm_calls("R1")] == ["simulated", "simulated"]


@pytest.mark.parametrize("first", [ModelResponse(parts=[TextPart("Here is your order.")]),
                                   ModelResponse(parts=[ToolCallPart("place_order", {"sku": "CAB-1"})])],
                         ids=["text-only", "unknown-tool"])
def test_text_or_unknown_tool_then_invalid_submit_is_invalid_model_output(env, first):
    store, run = env
    fn = Scripted(first, lambda info: ModelResponse(parts=[submit(info, BROKEN)]),
                  lambda info: ModelResponse(parts=[submit(info)]))
    with pytest.raises(ExtractionFailed) as exc:
        run(fn)
    assert exc.value.code == "INVALID_MODEL_OUTPUT"
    assert fn.calls == 2 and len(store.llm_calls("R1")) == 2


def test_bad_search_arguments_do_not_use_the_repair_budget(env):
    _, run = env
    bad = ModelResponse(parts=[ToolCallPart("search_catalog", {"q": "CAB-1"})])
    out = run(Scripted(bad, bad, bad, lambda info: ModelResponse(parts=[submit(info)])))
    assert len(out.llm_call_ids) == 4


def test_a_model_that_keeps_searching_hits_the_step_limit(env, settings):
    store, run = env
    fn = Scripted(ModelResponse(parts=[search()]))
    with pytest.raises(ExtractionFailed) as exc:
        run(fn)
    assert exc.value.code == "STEP_LIMIT"
    assert fn.calls == settings.max_steps == len(store.llm_calls("R1"))


def test_model_unavailable_simulation_makes_no_call(env):
    store, run = env
    fn = Scripted(ModelResponse(parts=[search()]))
    with pytest.raises(ExtractionFailed) as exc:
        run(fn, simulate="model_unavailable")
    assert exc.value.code == "MODEL_UNAVAILABLE" and fn.calls == 0
    assert [(c["source"], c["error"]) for c in store.llm_calls("R1")] == [("simulated", "simulated: model unavailable")]


def test_provider_http_error_is_model_unavailable_with_a_live_row(env):
    store, run = env

    def down(messages, info):
        raise ModelHTTPError(status_code=503, model_name="openai/gpt-6-luna", body={"error": "overloaded"})

    with pytest.raises(ExtractionFailed) as exc:
        run(down, provider="openrouter")
    assert exc.value.code == "MODEL_UNAVAILABLE" and "503" in str(exc.value)
    [row] = store.llm_calls("R1")
    assert (row["source"], row["provider"]) == ("live", "openrouter") and "ModelHTTPError" in row["error"]


def test_failed_run_still_stores_raw_submit_arguments(env):
    store, run = env
    with pytest.raises(ExtractionFailed):
        run(Scripted(lambda info: ModelResponse(parts=[submit(info, BROKEN)])))
    submits = [t for t in store.tool_calls("R1") if t["name"] == "submit_order_draft"]
    assert [t["arguments"] for t in submits] == [BROKEN, BROKEN]
    assert submits[0]["result"]["error"] == "invalid arguments"
    assert "INVALID_MODEL_OUTPUT" not in str(submits[1]["result"]) and submits[1]["result"]["error"]


def test_auto_mode_without_key_or_recording_is_model_unavailable(settings):
    store = Store(settings.db_path)
    auto = replace(settings, llm_mode="auto")
    req = parse_request_file(settings.requests_dir / "R1.txt", settings.root)
    model = ModelFactory()(auto, store, "R1")
    with pytest.raises(ExtractionFailed) as exc:
        extract(req, Catalog.load(settings.catalog_path), model, auto, store)
    assert exc.value.code == "MODEL_UNAVAILABLE"
    assert "No recorded response" in str(exc.value) and "OPENROUTER_API_KEY" in str(exc.value)
    assert [c["source"] for c in store.llm_calls("R1")] == ["live"]
