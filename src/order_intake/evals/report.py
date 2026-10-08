"""Judge-suite metrics and reports: calibration, noise floor, baseline comparison, Markdown and JSON."""

import hashlib
import json
from collections import Counter
from pathlib import Path

from ..config import Settings

JUDGED = ("pass", "review", "fail")
DETECTION_TARGET = 0.8
FALSE_FAIL_MAX = 1
UNAVAILABLE = ("judge_unavailable", "pipeline_unavailable")
SHORT = {"pass": "P", "fail": "F", "cannot_verify": "?"}


def rate(count: int, n: int) -> dict:
    return {"count": count, "n": n, "rate": count / n if n else None}


def cohen_kappa(labels: list[bool], predictions: list[bool]) -> float | None:
    """Binary Cohen's kappa; None (N/A) when there are no cases or every label is the same."""
    n = len(labels)
    if n == 0 or len(set(labels)) < 2:
        return None
    observed = sum(a == b for a, b in zip(labels, predictions, strict=True)) / n
    p_label, p_pred = sum(labels) / n, sum(predictions) / n
    chance = p_label * p_pred + (1 - p_label) * (1 - p_pred)
    return (observed - chance) / (1 - chance)


def calibration(rows: list[dict]) -> dict:
    """Metrics on the judge-only verdict; review counts as not-fail; abstentions and outages are excluded."""
    labelled = [r for r in rows if r["label"]]
    usable = [r for r in labelled if r["judge_only"] in JUDGED]
    defects = [r for r in usable if r["label"] == "fail"]
    clean = [r for r in usable if r["label"] == "pass"]
    classes = sorted({r["defect_class"] for r in labelled if r["defect_class"]})
    per_class = {}
    for cls in classes:
        judged = [r for r in defects if r["defect_class"] == cls]
        seeded = [r for r in labelled if r["defect_class"] == cls]
        per_class[cls] = {
            "judge": rate(sum(r["judge_only"] == "fail" for r in judged), len(judged)),
            "deterministic": rate(sum("fail" in (r["trajectory"]["verdict"], r["reference"]["verdict"])
                                      for r in seeded), len(seeded)),
        }
    detection = rate(sum(r["judge_only"] == "fail" for r in defects), len(defects))
    false_fail = rate(sum(r["judge_only"] == "fail" for r in clean), len(clean))
    labels = [r["label"] == "fail" for r in usable]
    predictions = [r["judge_only"] == "fail" for r in usable]
    excluded = [r["judge_only"] for r in labelled if r["judge_only"] not in JUDGED]
    return {
        "per_class": per_class, "detection": detection, "false_fail": false_fail,
        "agreement": rate(sum(a == b for a, b in zip(labels, predictions, strict=True)), len(usable)),
        "kappa": cohen_kappa(labels, predictions),
        "excluded": {"count": len(excluded), "by_reason": dict(sorted(Counter(excluded).items()))},
        "targets": {
            "detection": {"target": f">= {DETECTION_TARGET:.0%}",
                          "met": None if detection["rate"] is None else detection["rate"] >= DETECTION_TARGET},
            "false_fail": {"target": f"<= {FALSE_FAIL_MAX}",
                           "met": None if not clean else false_fail["count"] <= FALSE_FAIL_MAX},
        },
    }


def noise_floor(rows: list[dict]) -> dict:
    """Agreement between judge samples over all case criteria; a single sample has no noise floor."""
    agree = pairs = 0
    for r in rows:
        if r["judge"]["status"] == "judged":
            agree += r["judge"]["agreement"][0]
            pairs += r["judge"]["agreement"][1]
    return rate(agree, pairs)


def baseline_from(rows: list[dict], envelope: dict, samples: int) -> dict:
    return {"envelope": envelope, "samples": samples,
            "cases": {r["case_id"]: {c: v["verdict"] for c, v in r["judge"]["criteria"].items()}
                      for r in rows if r["judge"]["status"] == "judged"}}


def compare_baseline(baseline: dict | None, rows: list[dict], envelope: dict) -> dict:
    """A regression is a majority flip from pass to fail; other changes are counted, not flagged."""
    if baseline is None:
        return {"status": "no_baseline", "message": "no baseline", "regressions": []}
    before_env = baseline.get("envelope", {})
    if before_env != envelope:
        changed = [k for k in envelope if before_env.get(k) != envelope[k]]
        return {"status": "envelope_mismatch", "regressions": [],
                "message": f"warning: the baseline was made with a different {', '.join(changed)}; not compared"}
    regressions, improvements, changes, compared = [], [], 0, 0
    for r in rows:
        before = baseline["cases"].get(r["case_id"])
        if r["judge"]["status"] != "judged" or before is None:
            continue
        for criterion, now_ in r["judge"]["criteria"].items():
            if criterion not in before:
                continue
            compared += 1
            flip = {"case_id": r["case_id"], "criterion": criterion, "before": before[criterion],
                    "after": now_["verdict"], "samples": now_["samples"]}
            if before[criterion] == "pass" and now_["verdict"] == "fail":
                regressions.append(flip)
            elif before[criterion] == "fail" and now_["verdict"] == "pass":
                improvements.append(flip)
            elif before[criterion] != now_["verdict"]:
                changes += 1
    named = f", {len(improvements)} improvements" if improvements else ""
    return {"status": "compared", "regressions": regressions, "improvements": improvements, "other_changes": changes,
            "compared": compared,
            "message": f"{len(regressions)} regressions{named} in {compared} compared case criteria"}


def corpus_fingerprint(settings: Settings) -> str:
    """Hash of everything the verdicts depend on besides the judge model and rubric version."""
    files = [*sorted(p for p in settings.requests_dir.rglob("*") if p.is_file()),
             *sorted(p for p in settings.judge_cases_dir.rglob("*") if p.is_file()),
             settings.catalog_path, settings.data_dir / "reference" / "expected.json", settings.prompt_path,
             settings.judge_prompt_path]
    digest = hashlib.sha256()
    for path in files:
        name = path.relative_to(settings.root) if path.is_relative_to(settings.root) else Path(path.name)
        digest.update(f"{name.as_posix()}\0{hashlib.sha256(path.read_bytes()).hexdigest()}\n".encode())
    return digest.hexdigest()


def write_reports(results: dict, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "judge-results.json").write_text(json.dumps(results, indent=2, ensure_ascii=False) + "\n",
                                                encoding="utf-8")
    (out_dir / "judge-report.md").write_text(render_markdown(results), encoding="utf-8")


def _rate(r: dict) -> str:
    return f"{r['count']}/{r['n']} ({r['rate']:.0%})" if r["rate"] is not None else f"{r['count']}/{r['n']} (N/A)"


def _met(met: bool | None) -> str:
    return "N/A" if met is None else "met" if met else "**not met**"


def _cell(text: object, limit: int = 90) -> str:
    text = " ".join(str(text or "").split())
    text = text if len(text) <= limit else text[: limit - 1] + "…"
    return text.replace("|", "\\|")


def render_markdown(results: dict) -> str:
    env, cal, rows = results["envelope"], results["calibration"], results["cases"]
    criteria = results["criteria"]
    noise = results["noise_floor"]
    out = [
        "# LLM judge report",
        "",
        (f"Generated {results['generated_at']} by `uv run order-intake judge` · judge `{env['judge_model_id']}` · "
         f"rubric v{env['rubric_version']} · {results['samples']} samples per case · judge mode "
         f"`{results['judge_mode']}` · pipeline mode `{results['pipeline_mode']}` · pipeline model "
         f"`{results['model_id']}`."),
        ("The judge is blind: it sees the request (text or image), the catalog, the rules and the validated "
         "result, never the expected answers, case names, defect labels or the trajectory. Its evidence quotes "
         "are checked in code, and a deterministic failure always overrides it. The judge is an offline "
         "evaluation tool and never changes an order."),
        "",
        f"**Exit code: {results['exit_code']}**"
        + (f" (no recording for: {', '.join(results['missing_recordings'])})" if results["missing_recordings"]
           else ""),
        "",
        "## Summary",
        "",
        "| Overall verdict | Cases |",
        "|---|---|",
    ]
    for verdict, n in sorted(Counter(r["overall"] for r in rows).items()):
        out.append(f"| {verdict} | {n} |")
    out += [
        "",
        "## Calibration (judge-only verdict)",
        "",
        (f"Judge-only detection of seeded defects: **{_rate(cal['detection'])}**, target "
         f"{cal['targets']['detection']['target']}: {_met(cal['targets']['detection']['met'])}. Deterministic "
         "checks already catch some classes, so the judge-only figure is the headline."),
        "",
        "| Defect class | Judge-only detected | Deterministic checks caught |",
        "|---|---|---|",
    ]
    for cls, r in cal["per_class"].items():
        out.append(f"| {cls} | {_rate(r['judge'])} | {_rate(r['deterministic'])} |")
    kappa = "N/A" if cal["kappa"] is None else f"{cal['kappa']:.2f}"
    excluded = ", ".join(f"{k} {v}" for k, v in cal["excluded"]["by_reason"].items()) or "none"
    out += [
        "",
        (f"- False fail on clean labelled cases: {_rate(cal['false_fail'])}, target "
         f"{cal['targets']['false_fail']['target']}: {_met(cal['targets']['false_fail']['met'])}."),
        f"- Agreement with the labels: {_rate(cal['agreement'])}; Cohen's kappa per case: {kappa}.",
        f"- Excluded from calibration: {cal['excluded']['count']} ({excluded}). `review` counts as not-fail.",
        "- Targets are reported, not gated.",
        "",
        "## Noise floor",
        "",
        (f"Agreement between judge samples: {_rate(noise)} sample pairs over all judged case criteria. "
         "The baseline keeps the majority, so a single-sample flip is noise, not drift."),
        "",
        "## Baseline comparison",
        "",
        results["baseline"]["message"] + (f" ({results['baseline']['update']})"
                                          if results["baseline"].get("update") else ""),
        "",
    ]
    flips = [("regression", g) for g in results["baseline"]["regressions"]]
    flips += [("improvement", g) for g in results["baseline"].get("improvements", [])]
    if flips:
        out += ["| Change | Case | Criterion | Before | After | Samples |", "|---|---|---|---|---|---|"]
        for kind, g in flips:
            out.append(f"| {kind} | {g['case_id']} | {g['criterion']} | {g['before']} | {g['after']} | "
                       f"{', '.join(g['samples'])} |")
        out.append("")
    legend = ", ".join(f"{i + 1} {c}" for i, c in enumerate(criteria))
    out += [
        "## Cases",
        "",
        f"Judge criteria by position: {legend}; P pass, F fail, ? cannot verify.",
        "",
        "| Case | Kind | Label | Status | Trajectory | Reference | Judge criteria | Judge-only | Overall |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        crit = r["judge"].get("criteria")
        marks = " ".join(SHORT[crit[c]["verdict"]] for c in criteria) if crit else r["judge"]["status"]
        kind = r["kind"] + (f": {r['defect_class']}" if r["defect_class"] else "")
        out.append(f"| {r['case_id']} | {kind} | {r['label'] or '-'} | {r['status']} | "
                   f"{r['trajectory']['verdict']} | {r['reference']['verdict']} | {marks} | {r['judge_only']} | "
                   f"**{r['overall']}** |")
    out += ["", "## Deterministic failures", ""]
    failures = [(r["case_id"], name, r[name]["detail"]) for r in rows for name in ("trajectory", "reference")
                if r[name]["verdict"] in ("fail", "error")]
    out += [f"- {case} {name}: {_cell(detail, 200)}" for case, name, detail in failures] or ["None."]
    out += ["", "## Judge evidence", ""]
    for r in rows:
        crit = r["judge"].get("criteria")
        if not crit:
            if r["judge"].get("error"):
                out += [f"### {r['case_id']} — {r['judge']['status']}", "", _cell(r["judge"]["error"], 300), ""]
            continue
        review = " (unlabelled: review by hand)" if r["kind"] == "unlabelled" else ""
        out += [f"### {r['case_id']} — judge-only {r['judge_only']}{review}", "",
                "| Criterion | Verdict | Samples | Flags | Evidence | Rationale |", "|---|---|---|---|---|---|"]
        for c in criteria:
            v = crit[c]
            out.append(f"| {c} | {v['verdict']} | {', '.join(v['samples'])} | {_cell(', '.join(v['flags']))} | "
                       f"{_cell(v['evidence_quote'], 70)} | {_cell(v['rationale'], 140)} |")
        out.append("")
    out += [
        "## Evaluation envelope",
        "",
        f"- Judge model: `{env['judge_model_id']}`",
        f"- Rubric version: `{env['rubric_version']}`",
        (f"- Corpus fingerprint: `{env['corpus_fingerprint']}` (request files, judge cases, catalog, expected "
         "results and both prompts)"),
        f"- Judge model calls this run: {results['judge_model_calls']}",
        "",
        "A small fictional corpus; these figures are limited evidence about the judge on other inputs.",
        "",
    ]
    return "\n".join(out)
