from dataclasses import replace

import pytest

from order_intake.evals.judge import CRITERIA, majority, sample_agreement
from order_intake.evals.report import (
    baseline_from,
    calibration,
    cohen_kappa,
    compare_baseline,
    corpus_fingerprint,
    noise_floor,
    render_markdown,
)

pytestmark = pytest.mark.unit
ENV = {"judge_model_id": "deepseek/deepseek-v4.1-flash", "rubric_version": "1", "corpus_fingerprint": "abc"}


def judged(samples: dict[str, list[str]], verdict: str = "pass") -> dict:
    criteria = {c: {"verdict": majority(s), "samples": s, "evidence_quote": "q", "rationale": "r", "flags": []}
                for c, s in samples.items()}
    agreement = [sum(x) for x in zip(*(sample_agreement(s) for s in samples.values()), strict=True)]
    return {"status": "judged", "verdict": verdict, "criteria": criteria, "agreement": agreement}


def case(case_id, label, judge_only, defect_class=None, deterministic="pass", judge=None):
    return {"case_id": case_id, "label": label, "judge_only": judge_only, "defect_class": defect_class,
            "kind": "defect" if defect_class else "reference", "trajectory": {"verdict": "pass", "detail": ""},
            "reference": {"verdict": deterministic, "detail": "lines" if deterministic == "fail" else ""},
            "judge": judge or {"status": "judged" if judge_only in ("pass", "review", "fail") else judge_only,
                               "verdict": judge_only, "criteria": {}, "agreement": [0, 0]},
            "status": "ready_for_review", "overall": judge_only}


@pytest.mark.parametrize(("labels", "predictions", "kappa"), [
    ([1, 1, 1, 0, 0, 0], [1, 1, 0, 0, 0, 1], 1 / 3),
    ([1, 1, 0, 0], [1, 1, 0, 0], 1.0),
    ([1, 1, 0, 0], [0, 0, 1, 1], -1.0),
    ([1, 0, 1, 0], [0, 0, 0, 0], 0.0),
])
def test_kappa_on_known_lists(labels, predictions, kappa):
    assert cohen_kappa([bool(x) for x in labels], [bool(x) for x in predictions]) == pytest.approx(kappa)


@pytest.mark.parametrize("labels", [[True, True], [False, False, False], []])
def test_kappa_is_not_available_when_all_labels_are_equal(labels):
    assert cohen_kappa(labels, [True] * len(labels)) is None


def test_calibration_counts_review_as_not_fail_and_excludes_abstentions():
    rows = [
        case("D1", "fail", "fail", "wrong_sku", deterministic="fail"),
        case("D2", "fail", "review", "wrong_sku", deterministic="fail"),
        case("D3", "fail", "fail", "changed_quantity"),
        case("D4", "fail", "inconclusive", "changed_quantity"),
        case("D5", "fail", "judge_error", "invented_line", deterministic="fail"),
        case("R1", "pass", "pass"), case("R2", "pass", "fail"), case("R3", "pass", "review"),
        case("R4", "pass", "judge_unavailable"), case("J1", None, "fail"), case("R9", None, "not_judged"),
    ]
    cal = calibration(rows)
    assert cal["per_class"]["wrong_sku"] == {"judge": {"count": 1, "n": 2, "rate": 0.5},
                                            "deterministic": {"count": 2, "n": 2, "rate": 1.0}}
    assert cal["per_class"]["changed_quantity"]["judge"] == {"count": 1, "n": 1, "rate": 1.0}
    assert cal["per_class"]["invented_line"] == {"judge": {"count": 0, "n": 0, "rate": None},
                                                "deterministic": {"count": 1, "n": 1, "rate": 1.0}}
    assert cal["detection"] == {"count": 2, "n": 3, "rate": 2 / 3}
    assert cal["false_fail"] == {"count": 1, "n": 3, "rate": 1 / 3}
    assert cal["agreement"] == {"count": 4, "n": 6, "rate": 4 / 6}
    assert cal["kappa"] == pytest.approx(1 / 3)
    assert cal["excluded"] == {"count": 3, "by_reason": {"inconclusive": 1, "judge_error": 1,
                                                         "judge_unavailable": 1}}
    assert cal["targets"]["detection"]["met"] is False and cal["targets"]["false_fail"]["met"] is True


def test_calibration_with_nothing_judged_reports_not_available():
    cal = calibration([case("D1", "fail", "judge_unavailable", "wrong_sku")])
    assert cal["detection"]["rate"] is None and cal["kappa"] is None
    assert cal["targets"] == {"detection": {"target": ">= 80%", "met": None},
                              "false_fail": {"target": "<= 1", "met": None}}


def test_noise_floor_is_the_agreement_between_samples():
    rows = [case("R1", "pass", "pass", judge=judged({"product_mapping": ["pass", "pass", "fail"]})),
            case("R2", "pass", "pass", judge=judged({"product_mapping": ["pass", "pass", "pass"]})),
            case("R4", None, "not_judged")]
    assert noise_floor(rows) == {"count": 4, "n": 6, "rate": 4 / 6}


def test_majority_flips_are_regressions_or_improvements_and_a_single_sample_flip_is_neither():
    baseline = baseline_from([case("R1", "pass", "pass", judge=judged({"product_mapping": ["pass"] * 3,
                                                                        "quantity_fidelity": ["pass"] * 3,
                                                                        "ambiguity_handling": ["fail"] * 3}))],
                             ENV, 3)
    rows = [case("R1", "pass", "fail", judge=judged({"product_mapping": ["fail", "fail", "pass"],
                                                     "quantity_fidelity": ["pass", "pass", "fail"],
                                                     "ambiguity_handling": ["pass"] * 3}, "fail")),
            case("R2", "pass", "pass", judge=judged({"product_mapping": ["fail"] * 3}))]
    out = compare_baseline(baseline, rows, ENV)
    assert out["status"] == "compared" and out["compared"] == 3 and out["other_changes"] == 0
    assert out["regressions"] == [{"case_id": "R1", "criterion": "product_mapping", "before": "pass",
                                   "after": "fail", "samples": ["fail", "fail", "pass"]}]
    assert out["improvements"] == [{"case_id": "R1", "criterion": "ambiguity_handling", "before": "fail",
                                    "after": "pass", "samples": ["pass"] * 3}]
    assert out["message"] == "1 regressions, 1 improvements in 3 compared case criteria"


def test_no_baseline_is_reported_without_failing():
    assert compare_baseline(None, [], ENV) == {"status": "no_baseline", "message": "no baseline", "regressions": []}


@pytest.mark.parametrize("field", ["rubric_version", "judge_model_id", "corpus_fingerprint"])
def test_different_envelope_warns_and_skips_the_comparison(field):
    baseline = {"envelope": {**ENV, field: "other"}, "cases": {"R1": {"product_mapping": "pass"}}}
    rows = [case("R1", "pass", "fail", judge=judged({"product_mapping": ["fail"] * 3}, "fail"))]
    out = compare_baseline(baseline, rows, ENV)
    assert out["status"] == "envelope_mismatch" and out["regressions"] == []
    assert out["message"].startswith("warning:") and field in out["message"]


def test_fingerprint_covers_prompts_and_requests(settings, tmp_path):
    base = corpus_fingerprint(settings)
    assert base == corpus_fingerprint(settings)
    prompt = tmp_path / "judge.md"
    prompt.write_text(settings.judge_prompt_path.read_text() + "\nOne more rule.\n")
    assert corpus_fingerprint(replace(settings, judge_prompt_path=prompt)) != base
    cases = tmp_path / "cases"
    cases.mkdir()
    (cases / "J9.txt").write_text("Request-ID: J9\n")
    assert corpus_fingerprint(replace(settings, judge_cases_dir=cases)) != base


def test_markdown_report_shows_counts_regressions_and_evidence():
    crit = {c: ["pass", "fail", "fail"] if c == "product_mapping" else ["pass"] * 3 for c in CRITERIA}
    judge = judged(crit, "fail")
    judge["criteria"]["product_mapping"]["flags"] = ["quote_unverified"]
    rows = [case("D1-R6", "fail", "fail", "wrong_sku", deterministic="fail", judge=judge),
            {**case("J1", None, "judge_error"), "kind": "unlabelled",
             "judge": {"status": "judge_error", "error": "UnexpectedModelBehavior: retries | exceeded"}}]
    rows[1]["overall"] = "judge_error"
    results = {"generated_at": "now", "envelope": ENV, "model_id": "openai/gpt-6-luna", "judge_mode": "replay",
               "pipeline_mode": "replay", "samples": 3, "criteria": list(CRITERIA), "cases": rows,
               "calibration": calibration(rows), "noise_floor": noise_floor(rows), "judge_model_calls": 3,
               "baseline": {"status": "compared", "message": "1 regressions in 5 compared case criteria",
                            "update": "written to judge-baseline.json",
                            "regressions": [{"case_id": "D1-R6", "criterion": "product_mapping", "before": "pass",
                                             "after": "fail", "samples": ["pass", "fail", "fail"]}],
                            "improvements": [{"case_id": "J1", "criterion": "ambiguity_handling", "before": "fail",
                                              "after": "pass", "samples": ["pass"] * 3}]},
               "missing_recordings": [], "exit_code": 0}
    md = render_markdown(results)
    assert "Judge-only detection of seeded defects: **1/1 (100%)**" in md
    assert "| wrong_sku | 1/1 (100%) | 1/1 (100%) |" in md
    assert "Cohen's kappa per case: N/A" in md and "Excluded from calibration: 0 (none)" in md
    assert "| regression | D1-R6 | product_mapping | pass | fail | pass, fail, fail |" in md
    assert "| improvement | J1 | ambiguity_handling | fail | pass | pass, pass, pass |" in md
    assert "| D1-R6 | defect: wrong_sku | fail | ready_for_review | pass | fail | F P P P P | fail | **fail** |" in md
    assert "- D1-R6 reference: lines" in md and "quote_unverified" in md
    assert "### J1 — judge_error" in md and "retries \\| exceeded" in md
    assert "(written to judge-baseline.json)" in md
