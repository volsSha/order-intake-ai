import pytest

from order_intake.evals.trajectory import check_no_proposal, check_trajectory, proposal_trajectory

CAB1 = {"results": [{"sku": "CAB-1", "name": "USB-C cable 1 m"}]}
SUBMIT_CAB1 = {"lines": [{"sku": "CAB-1", "quantity": 2}], "clarification_draft": None, "notes": ""}


def call(name, arguments, result=None, llm_call_id="c1", row_id=1):
    return {"id": row_id, "name": name, "arguments": arguments, "result": result, "llm_call_id": llm_call_id}


def search(query="CAB-1", result=CAB1, **kw):
    return call("search_catalog", {"query": query}, result, **kw)


def submit(arguments=SUBMIT_CAB1, **kw):
    return call("submit_order_draft", arguments, "Final result processed.", **kw)


def failed(checks):
    return [c["name"] for c in checks if not c["passed"]]


@pytest.mark.integration
def test_scripted_search_then_submit_passes_every_check(make_pipeline):
    p = make_pipeline()
    p.process_all(only={"R6"})
    proposal = p.store.latest_proposal("R6")
    calls = proposal_trajectory(p.store.tool_calls("R6"), proposal["llm_call_ids"])
    assert [c["name"] for c in calls] == ["search_catalog", "search_catalog", "submit_order_draft"]
    checks = check_trajectory(calls, len(proposal["llm_call_ids"]), cap=6)
    assert len(checks) == 5 and failed(checks) == []


@pytest.mark.unit
def test_trajectory_uses_only_the_proposal_calls_in_step_and_emission_order():
    rows = [search(llm_call_id="old", row_id=1), submit(llm_call_id="b", row_id=5), search(llm_call_id="a", row_id=3),
            search("CAB-2", llm_call_id="a", row_id=4)]
    calls = proposal_trajectory(rows, ["a", "b"])
    assert [(c["llm_call_id"], c["id"]) for c in calls] == [("a", 3), ("a", 4), ("b", 5)]


@pytest.mark.unit
def test_submit_without_search_fails():
    checks = check_trajectory([submit()], 1, cap=6)
    assert failed(checks) == ["search before submit", "every submitted SKU searched"]


@pytest.mark.unit
def test_search_emitted_after_the_submit_does_not_count_as_before():
    assert "search before submit" in failed(check_trajectory([submit(), search()], 1, cap=6))


@pytest.mark.unit
def test_repeated_identical_search_is_flagged():
    checks = check_trajectory([search("CAB-1"), search(" cab-1 "), submit()], 2, cap=6)
    assert failed(checks) == ["no repeated identical searches"]


@pytest.mark.unit
@pytest.mark.parametrize(("requests", "ok"), [(5, True), (6, True), (7, False)])
def test_request_cap_boundary(requests, ok):
    checks = check_trajectory([search(), submit()], requests, cap=6)
    assert ("requests within cap" not in failed(checks)) is ok


@pytest.mark.unit
def test_two_submits_fail_the_trajectory():
    checks = check_trajectory([search(), submit(), submit()], 3, cap=6)
    assert failed(checks) == ["exactly one submit"]


@pytest.mark.unit
def test_submitted_sku_not_returned_by_any_search_fails():
    other = {"lines": [{"sku": "HUB-1", "quantity": 1}, {"sku": None, "quantity": None}]}
    assert failed(check_trajectory([search(), submit(other)], 2, cap=6)) == ["every submitted SKU searched"]


@pytest.mark.unit
def test_unparseable_submit_arguments_do_not_crash_the_sku_check():
    checks = check_trajectory([search(), submit('{"lines": "not-a-list"')], 2, cap=6)
    assert failed(checks) == []


@pytest.mark.unit
@pytest.mark.parametrize(("observed", "bad"), [
    ({"request": {"status": "duplicate"}, "proposal": None, "llm_calls": 0}, []),
    ({"request": {"status": "needs_clarification"}, "proposal": {"author": "system", "result": {"lines": []}},
      "llm_calls": 0}, []),
    ({"request": {"status": "failed", "status_reason": "UNUSABLE_INPUT: x"}, "proposal": None, "llm_calls": 1},
     ["model not called"]),
    ({"request": {"status": "failed", "status_reason": "MODEL_UNAVAILABLE: x"}, "proposal": None, "llm_calls": 1},
     []),
    ({"request": {"status": "duplicate"}, "proposal": {"author": "system", "result": {"lines": [{"sku": "X"}]}},
      "llm_calls": 0}, ["nothing invented"]),
])
def test_no_proposal_cases_get_deterministic_checks_only(observed, bad):
    assert failed(check_no_proposal(observed)) == bad


@pytest.mark.unit
def test_rejected_submit_then_accepted_submit_counts_as_one():
    rejected = call("submit_order_draft", '{"lines": "not-a-list"', {"error": "invalid arguments"})
    checks = check_trajectory([search(), rejected, submit()], 3, cap=6)
    assert failed(checks) == []
    assert next(c for c in checks if c["name"] == "exactly one submit")["detail"] == "1 accepted, 1 rejected"


@pytest.mark.unit
def test_only_rejected_submits_fail_exactly_one_submit():
    rejected = call("submit_order_draft", {"lines": []}, {"error": "invalid arguments"})
    assert "exactly one submit" in failed(check_trajectory([search(), rejected], 2, cap=6))
