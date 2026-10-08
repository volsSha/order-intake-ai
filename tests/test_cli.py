import pytest

from order_intake.cli import main, parse_simulate
from order_intake.evals.judge import run_judge

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("samples", ["0", "-1", "x"])
def test_judge_rejects_samples_below_one(samples, capsys):
    with pytest.raises(SystemExit) as exc:
        main(["judge", "--samples", samples])
    assert exc.value.code == 2
    assert "--samples" in capsys.readouterr().err


def test_run_judge_rejects_samples_below_one(settings, tmp_path):
    with pytest.raises(ValueError, match="at least 1"):
        run_judge(settings, tmp_path, samples=0)


def test_simulate_accepts_known_kinds_and_rejects_others():
    assert parse_simulate(["R1=model_unavailable", "R2=invalid_output"]) == {
        "R1": "model_unavailable", "R2": "invalid_output"}
    with pytest.raises(SystemExit, match="--simulate"):
        parse_simulate(["R1=boom"])


@pytest.mark.integration
def test_process_from_recordings_then_status(tmp_path, capsys):
    db = str(tmp_path / "cli.db")
    assert main(["process", "--mode", "replay", "--db", db, "--only", "R1,R2"]) == 0
    out = capsys.readouterr().out
    assert "R1    ready_for_review" in out and "R2    needs_clarification" in out
    assert main(["status", "--db", db]) == 0
    assert "needs_clarification=1, ready_for_review=1" in capsys.readouterr().out


@pytest.mark.integration
def test_process_with_a_labelled_simulated_outage(tmp_path, capsys):
    db = str(tmp_path / "sim.db")
    assert main(["process", "--mode", "replay", "--db", db, "--only", "R1", "--simulate", "R1=model_unavailable"]) == 0
    assert "failed" in capsys.readouterr().out


def test_check_exit_code_follows_the_report(monkeypatch, capsys):
    case = {"case_id": "REF-1", "request_id": "R1", "passed": False, "title": "normal order",
            "checks": [{"name": "total", "passed": False, "expected": 4000, "observed": 3000}]}
    report = {"cases": [case], "batch": [], "simulated_failures": [], "passed": False}
    monkeypatch.setattr("order_intake.evals.check.run_checks", lambda settings, out: report)
    assert main(["check", "--mode", "replay"]) == 1
    assert "x total: expected 4000, observed 3000" in capsys.readouterr().out


def test_judge_prints_a_summary_and_returns_its_exit_code(monkeypatch, tmp_path, capsys):
    results = {"cases": [{"case_id": "R1", "kind": "reference", "trajectory": {"verdict": "pass"},
                          "reference": {"verdict": "pass"}, "judge_only": "pass", "overall": "pass"}],
               "calibration": {"detection": {"count": 6, "n": 6}, "false_fail": {"count": 0, "n": 9},
                               "excluded": {"count": 0}},
               "baseline": {"message": "0 regressions in 5 compared case criteria"},
               "missing_recordings": ["J9"], "exit_code": 1}
    monkeypatch.setattr("order_intake.evals.judge.run_judge", lambda *a, **kw: results)
    assert main(["judge", "--out", str(tmp_path)]) == 1
    out = capsys.readouterr().out
    assert "judge-only detection 6/6" in out and "missing recordings: J9" in out


def test_serve_passes_db_and_mode_to_the_app(monkeypatch):
    monkeypatch.setenv("DB_PATH", "unset")
    monkeypatch.setenv("LLM_MODE", "unset")
    seen = {}
    monkeypatch.setattr("uvicorn.run", lambda app, **kw: seen.update(app=app, **kw))
    assert main(["serve", "--db", "x.db", "--mode", "replay", "--port", "9000"]) == 0
    assert seen == {"app": "order_intake.web.app:app", "host": "127.0.0.1", "port": 9000, "reload": False}
    import os
    assert (os.environ["DB_PATH"], os.environ["LLM_MODE"]) == ("x.db", "replay")
