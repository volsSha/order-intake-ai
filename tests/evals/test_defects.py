import copy
import json

import pytest

from order_intake.domain.catalog import Catalog
from order_intake.evals.check import reference_verdict
from order_intake.evals.defects import DEFECTS, GRADER_NOTE, seed_defects, wrong_sku
from order_intake.evals.judge import dataset_cases, judge_prompt, snapshot

pytestmark = pytest.mark.integration


@pytest.fixture
def clean(make_pipeline, settings):
    p = make_pipeline()
    p.process_all()
    cases = {r["request_id"]: snapshot(p.store, r) for r in p.store.list_requests()}
    ref = json.loads((settings.data_dir / "reference" / "expected.json").read_text(encoding="utf-8"))
    return cases, Catalog.load(settings.catalog_path), {c["request_id"]: c["expected"] for c in ref["cases"]}


def lines(case):
    return case.observed["proposal"]["result"]["lines"]


def test_every_defect_class_is_seeded_on_a_copy(clean):
    cases, catalog, _ = clean
    before = copy.deepcopy(cases)
    seeded = seed_defects(cases, catalog)
    assert [(c.case_id, cls) for c, cls in seeded] == [(f"D{n}-{base}", cls)
                                                       for n, (cls, base, _) in enumerate(DEFECTS, 1)]
    assert cases == before


def test_mutations_change_what_each_class_names(clean):
    cases, catalog, expected = clean
    d = {cls: c for c, cls in seed_defects(cases, catalog)}
    assert (lines(d["wrong_sku"])[1]["sku"], lines(d["wrong_sku"])[1]["name"]) == ("CAB-1", "USB-C cable 1 m")
    assert lines(d["changed_quantity"])[0]["quantity"] == 21
    assert lines(d["changed_quantity"])[0]["total_cents"] == 21 * 5000 - 10500
    matched = d["ambiguous_marked_matched"]
    assert lines(matched)[0]["sku"] == "CAB-1" and matched.observed["proposal"]["clarification"] is None
    assert matched.observed["request"]["status"] == "ready_for_review"
    assert matched.observed["proposal"]["result"]["findings"] == []
    assert "out of stock" in d["clarification_implies_unknown_exists"].observed["proposal"]["clarification"]
    assert [ln["sku"] for ln in lines(d["invented_line"])] == ["CAB-1", "CAB-2"]
    assert d["invented_line"].observed["proposal"]["order_total_cents"] == 4000 + 4 * 3000
    injected = d["grader_injection_with_wrong_quantity"]
    assert injected.body.endswith(GRADER_NOTE) and lines(injected)[0]["quantity"] == 11
    caught = {cls for cls, c in d.items() if reference_verdict(expected[c.request_id], c.observed)[0] == "fail"}
    assert caught == set(d) - {"clarification_implies_unknown_exists"}


def test_labels_stay_in_metadata_and_never_reach_the_prompt(clean, settings):
    cases, catalog, expected = clean
    seeded = seed_defects(cases, catalog)
    built = dataset_cases(list(cases.values()), seeded, expected)
    defects = [c for c in built if c.metadata["kind"] == "defect"]
    assert {c.metadata["label"] for c in defects} == {"fail"}
    for case in defects:
        text = judge_prompt(case.inputs, catalog, settings.root)[0]
        assert case.metadata["defect_class"] not in text and case.name not in text


def test_inapplicable_mutation_is_skipped_and_unavailable_base_is_kept(clean):
    cases, catalog, _ = clean
    assert not wrong_sku(copy.deepcopy(cases["R2"]), catalog)
    assert seed_defects(cases, catalog, defects=[("wrong_sku", "R2", wrong_sku)]) == []
    assert seed_defects(cases, catalog, defects=[("wrong_sku", "R4", wrong_sku)]) == []
    assert seed_defects(cases, catalog, defects=[("wrong_sku", "missing", wrong_sku)]) == []
    down = copy.deepcopy(cases["R6"])
    down.pipeline_unavailable = True
    [(kept, cls)] = seed_defects({"R6": down}, catalog, defects=[("wrong_sku", "R6", wrong_sku)])
    assert kept.pipeline_unavailable and lines(kept) == lines(cases["R6"]) and cls == "wrong_sku"
