"""Minimum-demonstration checks: compare application behaviour with data/reference/expected.json.

Expected values are read from the reference file (written independently); observed values come
from a fresh database built by the real pipeline. Nothing here feeds application output back
into the expectations.
"""

import json
import shutil
import tempfile
from dataclasses import replace
from pathlib import Path

from ..config import Settings
from ..llm.llm import ChatClient
from ..pipeline import Pipeline
from ..storage import Store, now


def _codes(store: Store, request_id: str) -> set[str]:
    req = store.get_request(request_id) or {}
    codes = set()
    reason = req.get("status_reason") or ""
    codes |= {c.strip() for c in reason.split(":")[0].split(",") if c.strip()}
    proposal = store.latest_proposal(request_id)
    if proposal:
        codes |= {f["code"] for f in proposal["result"]["findings"] if f["severity"] == "blocking"}
    return codes


def _lines(proposal: dict | None) -> list[dict]:
    if not proposal:
        return []
    keys = ("sku", "quantity", "unit_cents", "subtotal_cents", "discount_cents", "total_cents")
    return sorted(({k: line.get(k) for k in keys} for line in proposal["result"]["lines"]), key=lambda x: x["sku"] or "")


def _expected_lines(lines: list[dict]) -> list[dict]:
    return sorted(lines, key=lambda x: x["sku"])


def evaluate_case(case: dict, store: Store, pipeline: Pipeline, settings: Settings) -> dict:
    exp = case["expected"]
    rid = case["request_id"]
    req = store.get_request(rid) or {}
    checks: list[tuple[str, object, object, bool]] = []

    def check(name, expected, observed, ok=None):
        checks.append((name, expected, observed, expected == observed if ok is None else ok))

    if "status" in exp:
        check("status", exp["status"], req.get("status"))
    if "lines" in exp:
        check("lines", _expected_lines(exp["lines"]), _lines(store.latest_proposal(rid)))
        check("order_total_cents", exp["order_total_cents"], (store.latest_proposal(rid) or {}).get("order_total_cents"))
    for group in exp.get("findings_any_of", []):
        observed = _codes(store, rid)
        check(f"finding one of {group}", group, sorted(observed), ok=bool(observed & set(group)))
    proposal = store.latest_proposal(rid)
    if exp.get("no_sku_assigned"):
        skus = [line["sku"] for line in (proposal or {}).get("result", {}).get("lines", [])]
        check("no SKU assigned", [], [s for s in skus if s], ok=not any(skus))
    if exp.get("no_quantity_assigned"):
        qtys = [line["quantity"] for line in (proposal or {}).get("result", {}).get("lines", [])]
        check("no quantity assigned", [], [q for q in qtys if q is not None], ok=all(q is None for q in qtys))
    if exp.get("clarification_draft_required"):
        text = (proposal or {}).get("clarification")
        check("clarification draft saved", "non-empty", (text or "")[:80], ok=bool(text and text.strip()))
    if "duplicate_of" in exp:
        check("duplicate_of", exp["duplicate_of"], req.get("related_request_id"))
    if "conflicts_with" in exp:
        check("conflicts_with", exp["conflicts_with"], req.get("related_request_id"))
    if exp.get("new_orders_created") == 0:
        owner = store.order_owner(req.get("order_ref") or "")
        check("no new order for this request", "owner is another request", owner, ok=owner != rid)
    if exp.get("llm_called") is False:
        check("model not called", 0, len(store.llm_calls(rid)))
    if exp.get("reprocess_all_twice_changes_order_count") is False:
        orders, calls = store.order_count(), store.conn.execute("SELECT COUNT(*) FROM llm_calls").fetchone()[0]
        pipeline.process_all()
        check("order count after reprocessing all", orders, store.order_count())
        check("model calls after reprocessing all", calls,
              store.conn.execute("SELECT COUNT(*) FROM llm_calls").fetchone()[0])
    if "status_before" in exp:
        check("status before correction", exp["status_before"], req.get("status"))
        for group in exp.get("findings_before_any_of", []):
            observed = _codes(store, rid)
            check(f"finding before one of {group}", group, sorted(observed), ok=bool(observed & set(group)))
        corr = exp["correction"]
        current = store.latest_proposal(rid)
        lines = [{"sku": line["sku"], "quantity": line["quantity"], "product_text": line["product_text"]}
                 for line in (current["result"]["lines"] if current else [])]
        if not lines:
            lines = [{"sku": None, "quantity": None}]
        lines[corr["line"]].update(sku=corr["sku"], quantity=corr["quantity"])
        pipeline.correct(rid, lines, note="check: reviewer correction from reference case", reviewer="check")
        store.close()
        reopened = Store(settings.db_path)  # simulate an application restart
        pipeline.store = reopened
        after = reopened.latest_proposal(rid)
        check("status after correction (after restart)", exp["status_after"], reopened.get_request(rid)["status"])
        check("lines after correction (after restart)", _expected_lines(exp["lines_after"]), _lines(after))
        check("total after correction (after restart)", exp["order_total_after_cents"],
              after["order_total_cents"] if after else None)
        versions = reopened.proposals(rid)
        check("versions kept", f">= {exp['history_versions_min']}", len(versions),
              ok=len(versions) >= exp["history_versions_min"])
        store = reopened
    sources = sorted({c["source"] for c in store.llm_calls(rid)})
    return {"case_id": case["case_id"], "title": case["title"], "request_id": rid,
            "minimum_demonstration": case["minimum_demonstration"], "calculation": case["calculation"],
            "model_sources": sources,
            "checks": [{"name": n, "expected": e, "observed": o, "passed": ok} for n, e, o, ok in checks],
            "passed": all(ok for *_, ok in checks)}


def run_checks(settings: Settings, out_dir: Path, client_factory=ChatClient) -> dict:
    ref = json.loads((settings.data_dir / "reference" / "expected.json").read_text(encoding="utf-8"))
    work = Path(tempfile.mkdtemp(prefix="order-intake-check-"))
    try:
        main_settings = replace(settings, db_path=work / "check.db")
        store = Store(main_settings.db_path)
        pipeline = Pipeline(main_settings, store, client=client_factory(main_settings))
        processing = pipeline.process_all()
        counts_before = store.status_counts()
        orders_before = store.order_count()
        results = []
        for case in ref["cases"]:
            results.append(evaluate_case(case, pipeline.store, pipeline, main_settings))
        store = pipeline.store

        batch = ref["batch"]
        batch_checks = [
            {"name": "status counts before review", "expected": batch["status_counts_before_review"],
             "observed": counts_before, "passed": counts_before == batch["status_counts_before_review"]},
            {"name": "new orders", "expected": batch["new_orders"], "observed": orders_before,
             "passed": orders_before == batch["new_orders"]},
        ]

        sim_settings = replace(settings, db_path=work / "simulated.db")
        sim_store = Store(sim_settings.db_path)
        sim = Pipeline(sim_settings, sim_store,
                       client=client_factory(sim_settings, simulate={"R1": "model_unavailable", "R5": "invalid_output"}))
        sim.process_all(only={"R1", "R2", "R5"})
        r1, r2, r5 = (sim_store.get_request(x) for x in ("R1", "R2", "R5"))
        simulated = [
            {"name": "R1 simulated model outage -> failed, nothing invented", "expected": "failed MODEL_UNAVAILABLE",
             "observed": f"{r1['status']} {r1['status_reason'][:60]}",
             "passed": r1["status"] == "failed" and r1["status_reason"].startswith("MODEL_UNAVAILABLE")
             and sim_store.latest_proposal("R1") is None},
            {"name": "R5 simulated invalid output -> failed after one retry", "expected": "failed INVALID_MODEL_OUTPUT",
             "observed": f"{r5['status']} {r5['status_reason'][:60]}",
             "passed": r5["status"] == "failed" and r5["status_reason"].startswith("INVALID_MODEL_OUTPUT")},
            {"name": "R2 unaffected by other failures", "expected": "needs_clarification", "observed": r2["status"],
             "passed": r2["status"] == "needs_clarification"},
        ]
        sim_store.close()
        store.close()
    finally:
        shutil.rmtree(work, ignore_errors=True)

    report = {
        "generated_at": now(), "model_id": settings.model_id, "llm_mode": settings.llm_mode,
        "processing": processing, "cases": results, "batch": batch_checks, "simulated_failures": simulated,
        "passed": all(r["passed"] for r in results) and all(c["passed"] for c in batch_checks + simulated),
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "check-results.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    (out_dir / "minimum-demonstration.md").write_text(render_markdown(report), encoding="utf-8")
    return report


def _fmt(value) -> str:
    text = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value
    return text.replace("|", "\\|")


def render_markdown(report: dict) -> str:
    out = [
        "# Minimum demonstration — check results",
        "",
        (f"Generated {report['generated_at']} by `uv run order-intake check` · model `{report['model_id']}` · "
         f"LLM mode `{report['llm_mode']}`."),
        ("Expected values come from `data/reference/expected.json` (written and verified independently of the "
         "app); observed values come from a fresh database built by the real pipeline."),
        "",
        f"**Overall: {'PASS' if report['passed'] else 'FAIL — see failed rows below'}**",
        "",
        "## Summary",
        "",
        "| Case | Request | Scenario | Min. demo | Model responses | Result |",
        "|---|---|---|---|---|---|",
    ]
    for r in report["cases"]:
        out.append(f"| {r['case_id']} | {r['request_id']} | {r['title']} | {'yes' if r['minimum_demonstration'] else ''} | "
                   f"{', '.join(r['model_sources']) or 'not called'} | {'PASS' if r['passed'] else '**FAIL**'} |")
    out += ["", "## Batch", "", "| Check | Expected | Observed | Result |", "|---|---|---|---|"]
    for c in report["batch"]:
        out.append(f"| {c['name']} | {_fmt(c['expected'])} | {_fmt(c['observed'])} | {'PASS' if c['passed'] else '**FAIL**'} |")
    out += ["", "## Simulated failures (labelled, no API call)", "", "| Check | Expected | Observed | Result |",
            "|---|---|---|---|"]
    for c in report["simulated_failures"]:
        out.append(f"| {c['name']} | {_fmt(c['expected'])} | {_fmt(c['observed'])} | {'PASS' if c['passed'] else '**FAIL**'} |")
    out += ["", "## Case details", ""]
    for r in report["cases"]:
        out += [f"### {r['case_id']} · {r['request_id']} · {r['title']} — {'PASS' if r['passed'] else 'FAIL'}", "",
                f"Calculation: {r['calculation']}", "", "| Check | Expected | Observed | Result |", "|---|---|---|---|"]
        for c in r["checks"]:
            out.append(f"| {c['name']} | {_fmt(c['expected'])} | {_fmt(c['observed'])} | {'PASS' if c['passed'] else '**FAIL**'} |")
        out.append("")
    out += ["## Processing log", "", "| Request | Status | Action |", "|---|---|---|"]
    for p in report["processing"]:
        out.append(f"| {p['request_id']} | {p['status']} | {_fmt(p['action'])} |")
    out += ["", "Twelve fictional requests are a demonstration set; passing them is limited evidence about other inputs.", ""]
    return "\n".join(out)
