"""Deterministic trajectory checks: they flag agent drift from the tool calls behind one proposal."""

from dataclasses import dataclass

from pydantic_evals.evaluators import EvaluationReason, Evaluator, EvaluatorContext

from ..llm.agent import SEARCH, SUBMIT

MODEL_FAILURES = ("MODEL_UNAVAILABLE", "REPLAY_MISSING", "INVALID_MODEL_OUTPUT", "STEP_LIMIT")


def proposal_trajectory(tool_calls: list[dict], llm_call_ids: list[str]) -> list[dict]:
    """The tool calls of the proposal's model calls, in step and emission order (never older attempts)."""
    order = {call_id: i for i, call_id in enumerate(llm_call_ids)}
    linked = [t for t in tool_calls if t.get("llm_call_id") in order]
    return sorted(linked, key=lambda t: (order[t["llm_call_id"]], t["id"]))


def _query(call: dict) -> str:
    args = call.get("arguments")
    return " ".join(str(args.get("query", "")).lower().split()) if isinstance(args, dict) else ""


def _returned_skus(call: dict) -> set[str]:
    result = call.get("result")
    rows = result.get("results", []) if isinstance(result, dict) else []
    return {r["sku"] for r in rows if isinstance(r, dict) and r.get("sku")}


def _submitted_skus(call: dict) -> set[str]:
    args = call.get("arguments")
    lines = args.get("lines") if isinstance(args, dict) else None
    if not isinstance(lines, list):
        return set()
    return {ln["sku"] for ln in lines if isinstance(ln, dict) and ln.get("sku")}


def _check(name: str, passed: bool, detail: str = "") -> dict:
    return {"name": name, "passed": passed, "detail": detail}


def check_trajectory(calls: list[dict], model_requests: int, cap: int) -> list[dict]:
    names = [c["name"] for c in calls]
    submits = [i for i, n in enumerate(names) if n == SUBMIT]
    first_submit = submits[0] if submits else len(calls)
    searches = [c for c in calls if c["name"] == SEARCH]
    queries = [_query(c) for c in searches]
    repeated = sorted({q for q in queries if queries.count(q) > 1})
    returned: set[str] = set().union(*(_returned_skus(c) for c in searches))
    submitted: set[str] = set().union(*(_submitted_skus(calls[i]) for i in submits))
    unsearched = sorted(submitted - returned)
    return [
        _check("requests within cap", model_requests <= cap, f"{model_requests} of {cap}"),
        _check("search before submit", bool(submits) and SEARCH in names[:first_submit]),
        _check("every submitted SKU searched", bool(submits) and bool(searches) and not unsearched,
               ", ".join(unsearched)),
        _check("no repeated identical searches", not repeated, ", ".join(repeated)),
        _check("exactly one submit", len(submits) == 1, f"{len(submits)} submits"),
    ]


def check_no_proposal(observed: dict) -> list[dict]:
    """Duplicate, conflict and failed requests: nothing invented, and no model call unless the model failed."""
    req = observed["request"]
    proposal = observed.get("proposal")
    invented = bool(proposal and (proposal.get("author") == "model" or proposal["result"].get("lines")))
    checks = [_check("nothing invented", not invented)]
    model_failed = req.get("status") == "failed" and (req.get("status_reason") or "").startswith(MODEL_FAILURES)
    if not model_failed:
        checks.append(_check("model not called", observed["llm_calls"] == 0, f"{observed['llm_calls']} calls"))
    return checks


def summarize(checks: list[dict]) -> EvaluationReason:
    bad = [c["name"] + (f" ({c['detail']})" if c["detail"] else "") for c in checks if not c["passed"]]
    return EvaluationReason("fail" if bad else "pass", "; ".join(bad) or "all checks pass")


@dataclass(repr=False)
class TrajectoryEvaluator(Evaluator):
    cap: int = 6

    def evaluate(self, ctx: EvaluatorContext) -> dict:
        case = ctx.output
        if case.pipeline_unavailable:
            return {"trajectory": EvaluationReason("n/a", "pipeline unavailable")}
        if case.model_proposal is None:
            return {"trajectory": summarize(check_no_proposal(case.observed))}
        return {"trajectory": summarize(check_trajectory(case.trajectory, case.model_requests, self.cap))}
