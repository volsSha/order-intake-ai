import copy
import json
from types import SimpleNamespace

import pytest
from pydantic_evals import Case, Dataset

from order_intake.evals.check import ReferenceEvaluator, observe, reference_verdict

pytestmark = pytest.mark.integration


@pytest.fixture
def processed(make_pipeline, settings):
    p = make_pipeline()
    p.process_all()
    ref = json.loads((settings.data_dir / "reference" / "expected.json").read_text(encoding="utf-8"))
    expected = {c["request_id"]: c["expected"] for c in ref["cases"]}
    return p.store, expected


def test_clean_scripted_results_agree_with_the_reference(processed):
    store, expected = processed
    for rid in ("R1", "R2", "R3", "R5", "R6", "R7", "R8", "R10", "R11", "R12"):
        assert reference_verdict(expected[rid], observe(store, rid)) == ("pass", []), rid


def test_mutated_quantity_fails_reference_agreement(processed):
    store, expected = processed
    observed = copy.deepcopy(observe(store, "R5"))
    observed["proposal"]["result"]["lines"][0]["quantity"] = 21
    verdict, failed = reference_verdict(expected["R5"], observed)
    assert verdict == "fail" and failed == ["lines"]
    assert observe(store, "R5")["proposal"]["result"]["lines"][0]["quantity"] == 12


@pytest.mark.parametrize("rid", ["R4", "R9"])
def test_duplicate_and_conflict_pass_with_no_model_proposal(processed, rid):
    store, expected = processed
    observed = observe(store, rid)
    assert observed["proposal"] is None or observed["proposal"]["author"] == "system"
    assert reference_verdict(expected[rid], observed) == ("pass", [])


def test_reference_evaluator_is_a_thin_adapter(processed):
    store, expected = processed
    mutated = copy.deepcopy(observe(store, "R5"))
    mutated["proposal"]["result"]["lines"][0]["quantity"] = 21
    cases = [Case(name="clean", inputs=SimpleNamespace(observed=observe(store, "R5")), expected_output=expected["R5"]),
             Case(name="mutated", inputs=SimpleNamespace(observed=mutated), expected_output=expected["R5"]),
             Case(name="unlabelled", inputs=SimpleNamespace(observed=observe(store, "R1")))]
    report = Dataset(name="t", cases=cases, evaluators=[ReferenceEvaluator()]).evaluate_sync(
        lambda c: c, progress=False)
    labels = {c.name: (c.labels["reference"].value, c.labels["reference"].reason) for c in report.cases}
    assert labels == {"clean": ("pass", "all checks pass"), "mutated": ("fail", "lines"),
                      "unlabelled": ("n/a", "no reference case")}
