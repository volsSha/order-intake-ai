import pytest

from order_intake.pipeline import ReviewError
from order_intake.storage import Store

pytestmark = pytest.mark.integration


def statuses(pipeline):
    return {r["request_id"]: r["status"] for r in pipeline.store.list_requests()}


def test_batch_statuses_match_reference(make_pipeline):
    p = make_pipeline()
    p.process_all()
    s = statuses(p)
    assert s == {
        "R1": "ready_for_review", "R2": "needs_clarification", "R3": "needs_clarification", "R4": "duplicate",
        "R5": "ready_for_review", "R6": "ready_for_review", "R7": "needs_clarification",
        "R8": "needs_clarification", "R9": "needs_clarification", "R10": "ready_for_review",
        "R11": "ready_for_review", "R12": "failed",
    }
    assert p.store.order_count() == 9
    assert p.store.latest_proposal("R6")["order_total_cents"] == 42000
    assert p.store.latest_proposal("R10")["order_total_cents"] == 6000


def test_duplicate_and_conflict_do_not_call_the_model_or_create_orders(make_pipeline):
    p = make_pipeline()
    p.process_all(only={"R1", "R5"})
    calls_before, orders_before = p.client.calls, p.store.order_count()
    p.process_all(only={"R4", "R9"})
    assert p.client.calls == calls_before
    assert p.store.order_count() == orders_before
    assert p.store.get_request("R4")["related_request_id"] == "R1"
    assert p.store.get_request("R9")["status_reason"] == "CONFLICTING_ORDER_REF"
    assert p.store.latest_proposal("R5")["version"] == 1  # existing draft untouched


def test_reprocessing_everything_is_idempotent(make_pipeline):
    p = make_pipeline()
    p.process_all()
    snapshot, orders, calls = statuses(p), p.store.order_count(), p.client.calls
    again = p.process_all()
    assert statuses(p) == snapshot
    assert p.store.order_count() == orders
    assert p.client.calls == calls
    assert all(r["action"].startswith("skipped") for r in again)


def test_unusable_input_does_not_stop_the_batch(make_pipeline):
    p = make_pipeline()
    p.process_all(only={"R11", "R12", "R1"})
    assert statuses(p) == {"R1": "ready_for_review", "R11": "ready_for_review", "R12": "failed"}
    assert p.store.get_request("R12")["status_reason"].startswith("UNUSABLE_INPUT")


def test_model_unavailable_is_visible_and_retryable(make_pipeline):
    p = make_pipeline(simulate={"R1": "model_unavailable"})
    p.process_all(only={"R1", "R2"})
    req = p.store.get_request("R1")
    assert req["status"] == "failed" and req["status_reason"].startswith("MODEL_UNAVAILABLE")
    assert p.store.latest_proposal("R1") is None
    assert [c["source"] for c in p.store.llm_calls("R1")] == ["simulated"]
    assert statuses(p)["R2"] == "needs_clarification"
    p.client.simulate = {}
    p.process_all(only={"R1"}, retry_failed=True)
    assert p.store.get_request("R1")["status"] == "ready_for_review"
    assert p.store.order_count() == 2


def test_invalid_model_output_fails_after_one_bounded_retry(make_pipeline):
    p = make_pipeline(simulate={"R1": "invalid_output"})
    p.process_all(only={"R1"})
    req = p.store.get_request("R1")
    assert req["status"] == "failed" and req["status_reason"].startswith("INVALID_MODEL_OUTPUT")
    assert len(p.store.llm_calls("R1")) == 2


def test_model_guess_without_lookup_is_rejected(make_pipeline):
    script = {"R1": [{"product_text": "CAB-1 cables", "product_status": "matched", "sku": "HUB-1",
                      "candidate_skus": [], "quantity_text": "2", "quantity_status": "explicit", "quantity": 2,
                      "unit": "item"}]}
    p = make_pipeline(script=script)
    p.process_all(only={"R1"})
    findings = {f["code"] for f in p.store.latest_proposal("R1")["result"]["findings"]}
    assert "PRODUCT_MISMATCH" in findings
    assert p.store.get_request("R1")["status"] == "needs_clarification"


def test_reviewer_correction_reruns_validation_and_survives_restart(make_pipeline, settings):
    p = make_pipeline()
    p.process_all(only={"R7"})
    assert p.store.get_request("R7")["status"] == "needs_clarification"
    out = p.correct("R7", [{"sku": "cab-1", "quantity": 10}], note="Customer confirmed 1 m cables by phone")
    assert out["status"] == "ready_for_review"
    p.store.close()

    reopened = Store(settings.db_path)
    req = reopened.get_request("R7")
    versions = reopened.proposals("R7")
    assert req["status"] == "ready_for_review"
    assert [v["author"] for v in versions] == ["model", "reviewer"]
    assert versions[-1]["order_total_cents"] == 18000
    assert versions[-1]["note"] == "Customer confirmed 1 m cables by phone"
    assert any(a["action"] == "correction" for a in reopened.audit_entries("R7"))


def test_approval_requires_a_valid_draft(make_pipeline):
    p = make_pipeline()
    p.process_all(only={"R1", "R2", "R4"})
    p.approve("R1")
    assert p.store.get_request("R1")["status"] == "approved"
    for rid in ("R2", "R4"):
        with pytest.raises(ReviewError):
            p.approve(rid)


def test_correction_after_approval_requires_new_approval(make_pipeline):
    p = make_pipeline()
    p.process_all(only={"R1"})
    p.approve("R1")
    p.correct("R1", [{"sku": "CAB-1", "quantity": 3}])
    assert p.store.get_request("R1")["status"] == "ready_for_review"


def test_duplicate_request_cannot_be_corrected(make_pipeline):
    p = make_pipeline()
    p.process_all(only={"R1", "R4"})
    assert not p.can_edit("R4")


def test_check_runner_passes_with_a_well_behaved_model(settings, tmp_path):
    from order_intake.evals.check import run_checks
    from tests.conftest import FakeClient

    report = run_checks(settings, tmp_path / "reports", client_factory=FakeClient)
    failed = [(r["case_id"], c) for r in report["cases"] for c in r["checks"] if not c["passed"]]
    assert failed == []
    assert report["passed"], [c for c in report["batch"] + report["simulated_failures"] if not c["passed"]]
    assert (tmp_path / "reports" / "minimum-demonstration.md").read_text().count("PASS") > 12
