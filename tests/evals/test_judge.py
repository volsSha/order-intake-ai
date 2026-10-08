import json
import re
import shutil
from dataclasses import replace

import pytest
from pydantic_ai.messages import BinaryContent, UserPromptPart

from order_intake.cli import main
from order_intake.config import ConfigError, check_judge_model, load_settings, model_family
from order_intake.evals.defects import DEFECTS
from order_intake.evals.judge import (
    CRITERIA,
    CriterionVerdict,
    judge_only_verdict,
    majority,
    overall_verdict,
    quote_found,
    run_judge,
    sample_agreement,
    verify,
)
from tests.evals.conftest import FakeJudges, honest, request_of, verdicts


def row(rows, case_id):
    return next(r for r in rows if r["case_id"] == case_id)


def v(criterion, verdict, quote):
    return CriterionVerdict(criterion=criterion, verdict=verdict, evidence_quote=quote, rationale="r")


# --- pure verdict logic -------------------------------------------------------------------------------

@pytest.mark.unit
@pytest.mark.parametrize(("quote", "found"), [
    ("Please send 2 individual CAB-1 cables.", True),
    ("  please SEND 2\n individual  cab-1 cables ", True),
    ("“Moon adapter”", True),
    ("...individual CAB-1...", True),
    ("Please send 3 individual CAB-1 cables.", False),
    ("", False),
])
def test_quotes_are_matched_after_whitespace_case_and_quote_mark_normalisation(quote, found):
    assert quote_found(quote, ["Please send 2 individual CAB-1 cables.", 'Is "Moon adapter" right?']) is found


@pytest.mark.unit
def test_fabricated_quote_downgrades_only_that_criterion():
    sources = ["Please send 2 individual CAB-1 cables."]
    out = verify([v("product_mapping", "pass", "Please send 99 golden cables"),
                  v("quantity_fidelity", "fail", "send 2 individual"),
                  v("ambiguity_handling", "cannot_verify", "made up")], sources, image=False)
    assert out["product_mapping"]["verdict"] == "cannot_verify"
    assert out["product_mapping"]["flags"] == ["quote_not_found (judge said pass)"]
    assert (out["quantity_fidelity"]["verdict"], out["quantity_fidelity"]["flags"]) == ("fail", [])
    assert (out["ambiguity_handling"]["verdict"], out["ambiguity_handling"]["flags"]) == ("cannot_verify", [])


@pytest.mark.unit
def test_image_request_keeps_request_derived_verdicts_flagged_quote_unverified():
    out = verify([v("product_mapping", "pass", "CAB-2 x 4 (from the form)"),
                  v("clarification_quality", "pass", "not in the text")], ["Hello, our order form is attached."],
                 image=True)
    assert (out["product_mapping"]["verdict"], out["product_mapping"]["flags"]) == ("pass", ["quote_unverified"])
    assert out["clarification_quality"]["verdict"] == "cannot_verify"


@pytest.mark.unit
@pytest.mark.parametrize(("values", "expected"), [
    (["pass", "pass", "fail"], "pass"), (["fail", "fail", "pass"], "fail"),
    (["pass", "fail", "cannot_verify"], "cannot_verify"), (["pass", "fail"], "cannot_verify"), (["fail"], "fail"),
])
def test_majority_over_samples(values, expected):
    assert majority(values) == expected


@pytest.mark.unit
def test_sample_agreement_counts_agreeing_pairs():
    assert sample_agreement(["pass", "pass", "fail"]) == (1, 3)
    assert sample_agreement(["pass"]) == (0, 0)


@pytest.mark.unit
@pytest.mark.parametrize(("values", "expected"), [
    (["pass"] * 5, "pass"), (["pass"] * 4 + ["cannot_verify"], "review"),
    (["fail", "cannot_verify", "pass", "pass", "pass"], "fail"), (["cannot_verify"] * 5, "inconclusive"),
])
def test_judge_only_verdict(values, expected):
    assert judge_only_verdict(values) == expected


@pytest.mark.unit
@pytest.mark.parametrize(("deterministic", "judge", "pipeline_unavailable", "expected"), [
    (["fail", "pass"], {"status": "judged", "verdict": "pass"}, False, "fail"),
    (["pass", "error"], {"status": "judged", "verdict": "pass"}, False, "fail"),
    (["pass", "fail"], {"status": "judge_unavailable"}, False, "fail"),
    (["pass", "pass"], {"status": "judge_unavailable"}, False, "judge_unavailable"),
    (["pass", "n/a"], {"status": "judged", "verdict": "review"}, False, "review"),
    (["pass", "pass"], {"status": "not_judged"}, False, "pass"),
    (["n/a", "n/a"], {"status": "pipeline_unavailable"}, True, "pipeline_unavailable"),
])
def test_overall_verdict_deterministic_failure_wins(deterministic, judge, pipeline_unavailable, expected):
    assert overall_verdict(deterministic, judge, pipeline_unavailable) == expected


@pytest.mark.unit
def test_same_family_judge_is_a_config_error(settings, monkeypatch):
    assert model_family("deepseek/deepseek-v4.1-flash") == "deepseek"
    check_judge_model(settings)
    with pytest.raises(ConfigError, match="same family"):
        check_judge_model(replace(settings, judge_model_id="OpenAI/gpt-6-sol"))
    monkeypatch.setenv("MODEL_ID", "openai/gpt-6-luna")
    monkeypatch.setenv("JUDGE_MODEL_ID", "openai/gpt-6-sol")
    with pytest.raises(ConfigError):
        check_judge_model(load_settings())


# --- suite runs with FunctionModel judges -------------------------------------------------------------

@pytest.mark.integration
def test_judge_runs_end_to_end_and_writes_both_reports(judge_run, judge_settings, tmp_path):
    judges = FakeJudges()
    results = judge_run(judges, samples=3)
    rows = results["cases"]
    assert [r["case_id"] for r in rows][:13] == [f"R{i}" for i in range(1, 13)] + ["J1"]
    assert len(rows) == 13 + len(DEFECTS) and results["exit_code"] == 0
    assert row(rows, "R1")["overall"] == "pass" and row(rows, "R1")["judge"]["agreement"] == [15, 15]
    assert (row(rows, "R1")["judge"]["judge_model_id"], row(rows, "R1")["judge"]["rubric_version"]) == (
        judge_settings.judge_model_id, "1")
    assert {row(rows, r)["judge"]["status"] for r in ("R4", "R9", "R12")} == {"not_judged"}
    assert row(rows, "J1")["kind"] == "unlabelled" and row(rows, "J1")["judge_only"] == "pass"
    # a judge pass never hides a deterministic failure
    assert (row(rows, "D1-R6")["judge_only"], row(rows, "D1-R6")["overall"]) == ("pass", "fail")
    cal = results["calibration"]
    assert cal["detection"] == {"count": 0, "n": len(DEFECTS), "rate": 0.0}
    assert cal["false_fail"]["n"] == 9 and cal["kappa"] == 0.0
    assert results["noise_floor"]["rate"] == 1.0 and results["baseline"]["status"] == "no_baseline"
    out = tmp_path / "out"
    assert json.loads((out / "judge-results.json").read_text())["exit_code"] == 0
    md = (out / "judge-report.md").read_text()
    assert "## Calibration (judge-only verdict)" in md and "| D1-R6 | defect: wrong_sku |" in md
    assert not (out / "judge-baseline.json").exists()
    files = sorted((judge_settings.replay_dir / "judge").rglob("*.json"))
    assert len(files) == 3 * (9 + 1 + len(DEFECTS)) == len(judges.messages)

    replayed = judge_run(samples=3, mode="replay")
    assert replayed["judge_model_calls"] == len(files) and replayed["exit_code"] == 0
    assert [r["overall"] for r in replayed["cases"]] == [r["overall"] for r in rows]


@pytest.mark.integration
def test_same_case_on_two_fresh_databases_gets_the_same_judge_keys(judge_run, judge_settings, tmp_path):
    judge_run(FakeJudges())
    first = sorted(p.relative_to(judge_settings.replay_dir / "judge").as_posix()
                   for p in (judge_settings.replay_dir / "judge").rglob("*.json"))
    other = replace(judge_settings, replay_dir=tmp_path / "other-replay")
    shutil.copytree(judge_settings.replay_dir / "extract", other.replay_dir / "extract")
    judge_run(FakeJudges(), settings=other)
    second = sorted(p.relative_to(other.replay_dir / "judge").as_posix()
                    for p in (other.replay_dir / "judge").rglob("*.json"))
    assert first == second and any(f.startswith("R1/step01-") for f in first)


@pytest.mark.integration
def test_missing_judge_recording_marks_that_case_and_the_suite_continues(judge_run, judge_settings):
    judge_run(FakeJudges())
    shutil.rmtree(judge_settings.replay_dir / "judge" / "R5")
    results = judge_run(mode="replay")
    rows = results["cases"]
    assert row(rows, "R5")["judge"]["status"] == "judge_unavailable"
    assert "REPLAY_MISSING" in row(rows, "R5")["judge"]["error"]
    assert row(rows, "R6")["judge"]["status"] == "judged"
    assert results["exit_code"] == 1 and results["missing_recordings"] == ["R5"]
    assert results["calibration"]["excluded"]["by_reason"] == {"judge_unavailable": 1}


@pytest.mark.integration
def test_invalid_judge_schema_gets_one_retry_then_judge_error(judge_run):
    def decide(text):
        if "Moon adapter" in request_of(text):
            return {"verdicts": [{"criterion": "product_mapping", "verdict": "pass", "evidence_quote": "Moon",
                                  "rationale": "r"}]}
        return honest(text)

    judges = FakeJudges(decide)
    rows = judge_run(judges)["cases"]
    assert row(rows, "R2")["judge"]["status"] == "judge_error"
    assert "Exceeded maximum output retries" in row(rows, "R2")["judge"]["error"]
    assert row(rows, "R2")["overall"] == "judge_error" and row(rows, "R1")["overall"] == "pass"
    moon = [m for m in judges.messages if "Moon adapter" in str(m[0])]
    assert len(moon) == 4  # R2 and its defect copy, two requests each


@pytest.mark.integration
def test_all_abstaining_judge_is_inconclusive_and_left_out_of_kappa(judge_run):
    def decide(text):
        if "USB hubs" in request_of(text):
            return verdicts("USB hubs", default="cannot_verify")
        return honest(text)

    results = judge_run(FakeJudges(decide))
    assert row(results["cases"], "R5")["judge_only"] == "inconclusive"
    assert row(results["cases"], "R8")["overall"] == "inconclusive"
    excluded = results["calibration"]["excluded"]
    assert excluded == {"count": 3, "by_reason": {"inconclusive": 3}}  # R5, R8 and the R5 defect
    assert results["calibration"]["agreement"]["n"] == 9 + len(DEFECTS) - 3


@pytest.mark.integration
def test_fabricated_quote_in_a_run_gives_cannot_verify_and_review(judge_run):
    def decide(text):
        out = honest(text)
        out["verdicts"][0]["evidence_quote"] = "Please send 99 golden cables"
        return out

    r1 = row(judge_run(FakeJudges(decide))["cases"], "R1")
    assert r1["judge"]["criteria"]["product_mapping"]["verdict"] == "cannot_verify"
    assert r1["judge_only"] == "review" and r1["overall"] == "review"


@pytest.mark.integration
def test_image_case_is_judged_from_the_image_and_flagged(judge_run):
    judges = FakeJudges(lambda text: verdicts("CAB-2 x 4 on the order form"))
    r11 = row(judge_run(judges)["cases"], "R11")
    criteria = r11["judge"]["criteria"]
    assert all(criteria[c]["flags"] == ["quote_unverified"] and criteria[c]["verdict"] == "pass"
               for c in CRITERIA if c != "clarification_quality")
    assert criteria["clarification_quality"]["verdict"] == "cannot_verify" and r11["judge_only"] == "review"
    image_prompts = [m for m in judges.messages
                     if any(isinstance(c, BinaryContent) for p in m[0].parts if isinstance(p, UserPromptPart)
                            for c in p.content)]
    assert len(image_prompts) == 1


@pytest.mark.integration
def test_judge_is_blind_to_expected_values_names_labels_and_trajectory(judge_run, settings):
    judges = FakeJudges()
    results = judge_run(judges)
    prompts = [json.dumps([p.content if isinstance(p.content, str) else p.content[0]
                           for msg in m for p in msg.parts if isinstance(p, UserPromptPart)])
               for m in judges.messages]
    instructions = settings.judge_prompt_path.read_text()
    forbidden = ["REF-", "expected", "calculation", "search_catalog", '"query"', "candidate_skus", "llm_call",
                 "-s1-", "Request-ID", "Order-Ref", "2026-", "unit_cents", *[cls for cls, *_ in DEFECTS]]
    assert len(prompts) == 9 + 1 + len(DEFECTS) == len([r for r in results["cases"] if "criteria" in r["judge"]])
    for text in prompts:
        assert not [f for f in forbidden if f in text], text
        assert not re.search(r"\b[RDJ]\d+\b", text), text
    assert all(f not in instructions for f in [cls for cls, *_ in DEFECTS])


@pytest.mark.integration
def test_same_family_judge_stops_the_run(judge_settings, tmp_path):
    from order_intake.evals.judge import run_judge

    with pytest.raises(ConfigError):
        run_judge(replace(judge_settings, judge_model_id="openai/gpt-6-sol"), tmp_path)


@pytest.mark.integration
def test_baseline_is_written_only_on_request_and_compared_on_the_next_run(judge_run, tmp_path):
    first = judge_run(FakeJudges(), update_baseline=True)
    baseline = json.loads((tmp_path / "out" / "judge-baseline.json").read_text())
    assert first["baseline"]["status"] == "no_baseline" and "written" in first["baseline"]["update"]
    assert baseline["envelope"] == first["envelope"] and baseline["cases"]["R1"]["product_mapping"] == "pass"
    assert "R4" not in baseline["cases"]

    def worse(text):
        quote = request_of(text).splitlines()[0]
        return verdicts(quote, product_mapping="fail") if "Moon adapter" in quote else honest(text)

    second = judge_run(FakeJudges(worse))
    assert second["baseline"]["status"] == "compared" and second["exit_code"] == 0
    assert [(g["case_id"], g["criterion"]) for g in second["baseline"]["regressions"]] == [
        ("R2", "product_mapping"), ("D4-R2", "product_mapping")]
    assert "| R2 | product_mapping | pass | fail |" in (tmp_path / "out" / "judge-report.md").read_text()


@pytest.mark.integration
def test_baseline_is_not_updated_from_an_incomplete_run(judge_run, tmp_path):
    results = judge_run(mode="replay", update_baseline=True)
    assert results["exit_code"] == 1 and results["baseline"]["update"].startswith("not updated")
    assert not (tmp_path / "out" / "judge-baseline.json").exists()


@pytest.mark.integration
def test_missing_pipeline_recordings_are_unavailable_not_judged_and_block_the_baseline(judge_settings, tmp_path):
    empty = tmp_path / "empty-replay"
    empty.mkdir()
    results = run_judge(replace(judge_settings, replay_dir=empty), tmp_path / "out", judge_mode="live", samples=1,
                        update_baseline=True, judge_models=lambda: FakeJudges())
    unavailable = [r for r in results["cases"] if r["judge"]["status"] == "pipeline_unavailable"]
    assert unavailable and not any(r["judge"]["status"] == "judged" for r in results["cases"])
    assert {r["case_id"] for r in unavailable} <= set(results["missing_recordings"])
    assert results["exit_code"] == 1
    assert not (tmp_path / "out" / "judge-baseline.json").exists()


@pytest.mark.integration
def test_cli_judge_writes_reports_to_the_out_directory(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    code = main(["judge", "--out", str(tmp_path), "--samples", "1"])
    out = capsys.readouterr().out
    assert code == json.loads((tmp_path / "judge-results.json").read_text())["exit_code"]
    assert (tmp_path / "judge-report.md").is_file() and "judge-only detection" in out
