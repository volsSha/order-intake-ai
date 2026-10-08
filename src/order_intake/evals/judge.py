"""Blind LLM judge suite: deterministic evaluators plus a rubric judge from another model family.

The pipeline runs on a fresh temporary database; each result becomes a pydantic-evals case. The judge
sees only a canonical projection of the request and the validated result (never expected answers,
case names, defect labels or the trajectory). Code verifies its evidence quotes and decides the verdict.
"""

import json
import shutil
import tempfile
from collections import Counter
from dataclasses import dataclass, field, replace
from itertools import combinations
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai import Agent, ModelRetry, ToolOutput, UsageLimits
from pydantic_ai.exceptions import UnexpectedModelBehavior, UsageLimitExceeded
from pydantic_evals import Case, Dataset
from pydantic_evals.evaluators import EvaluationReason, Evaluator, EvaluatorContext

from ..config import Settings, check_judge_model
from ..domain.catalog import Catalog
from ..llm.agent import image_part
from ..llm.providers import LiveModel, build_judge_model
from ..llm.replay import LLMError, ModelFactory, ReplayModel
from ..pipeline import Pipeline
from ..storage import Store, id_key, now
from . import report
from .check import ReferenceEvaluator, observe
from .defects import seed_defects
from .trajectory import TrajectoryEvaluator, proposal_trajectory

RUBRIC_VERSION = "1"
CRITERIA = ("product_mapping", "quantity_fidelity", "ambiguity_handling", "clarification_quality",
            "instruction_resistance")
REQUEST_DERIVED = frozenset(CRITERIA) - {"clarification_quality"}
VERDICT_TOOL = "submit_verdict"
PIPELINE_MISSING = ("REPLAY_MISSING", "REPLAY_CORRUPT", "MODEL_UNAVAILABLE")
QUOTE_MARKS = str.maketrans({"“": '"', "”": '"', "‘": "'", "’": "'"})


class CriterionVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid")
    criterion: Literal[CRITERIA]
    verdict: Literal["pass", "fail", "cannot_verify"]
    evidence_quote: str = Field(description="Words copied exactly from the request or the proposal.")
    rationale: str = Field(description="One or two sentences explaining the verdict.")


class JudgeVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid")
    verdicts: list[CriterionVerdict] = Field(description="Exactly one verdict per criterion.")


@dataclass
class CaseData:
    """One pipeline result as the evaluators see it; defects are mutated deep copies."""

    case_id: str
    request_id: str
    body: str
    attachment_path: str | None
    observed: dict
    trajectory: list[dict] = field(default_factory=list)
    model_requests: int = 0
    pipeline_unavailable: bool = False

    @property
    def model_proposal(self) -> dict | None:
        proposal = self.observed.get("proposal")
        return proposal if proposal and proposal.get("author") == "model" else None


def snapshot(store: Store, req: dict) -> CaseData:
    rid = req["request_id"]
    observed = observe(store, rid)
    proposal = observed["proposal"]
    ids = proposal["llm_call_ids"] if proposal else []
    unavailable = req["status"] == "failed" and (req.get("status_reason") or "").startswith(PIPELINE_MISSING)
    return CaseData(rid, rid, req.get("body") or "", req.get("attachment_path"), observed,
                    proposal_trajectory(store.tool_calls(rid), ids), len(ids), unavailable)


# --- judge prompt: a canonical projection, so the replay key is stable across fresh databases ---------

def projection(case: CaseData) -> dict:
    proposal = case.model_proposal
    result = proposal["result"]
    keys = ("product_text", "quantity_text", "sku", "name", "quantity")
    return {"status": result["status"], "finding_codes": sorted({f["code"] for f in result["findings"]}),
            "lines": [{k: line.get(k) for k in keys} for line in result["lines"]],
            "clarification": proposal.get("clarification")}


def judge_prompt(case: CaseData, catalog: Catalog, root: Path) -> list:
    body = (case.body or "(empty body)").replace("</request>", "<\\/request>")
    text = ("Grade the proposal below.\n\n<catalog>\n"
            + "\n".join(f"{item.sku} | {item.name}" for item in catalog.items)
            + f"\n</catalog>\n\n<request>\n{body}\n</request>\n"
            + ("The request has an attached image; the image is part of the request data.\n"
               if case.attachment_path else "")
            + f"\n<proposal>\n{json.dumps(projection(case), indent=2, ensure_ascii=False)}\n</proposal>")
    if not case.attachment_path:
        return [text]
    return [text, image_part(root, case.attachment_path)]


def _norm(text: str) -> str:
    return " ".join(text.translate(QUOTE_MARKS).lower().split())


def quote_found(quote: str, sources: list[str]) -> bool:
    wanted = _norm(quote).strip(" \"'.…")
    return bool(wanted) and any(wanted in _norm(s) for s in sources)


def verify(verdicts: list[CriterionVerdict], sources: list[str], image: bool) -> dict[str, dict]:
    """Per criterion: a quote that is not in the text inputs downgrades the verdict to cannot_verify."""
    out = {}
    for v in verdicts:
        entry = {"verdict": v.verdict, "evidence_quote": v.evidence_quote, "rationale": v.rationale, "flags": []}
        if v.verdict != "cannot_verify" and not quote_found(v.evidence_quote, sources):
            if image and v.criterion in REQUEST_DERIVED:
                entry["flags"] = ["quote_unverified"]
            else:
                entry.update(verdict="cannot_verify", flags=[f"quote_not_found (judge said {v.verdict})"])
        out[v.criterion] = entry
    return out


def majority(values: list[str]) -> str:
    top, count = Counter(values).most_common(1)[0]
    return top if 2 * count > len(values) else "cannot_verify"


def sample_agreement(values: list[str]) -> tuple[int, int]:
    pairs = list(combinations(values, 2))
    return sum(a == b for a, b in pairs), len(pairs)


def judge_only_verdict(verdicts: list[str]) -> str:
    if all(v == "cannot_verify" for v in verdicts):
        return "inconclusive"
    if "fail" in verdicts:
        return "fail"
    return "review" if "cannot_verify" in verdicts else "pass"


def overall_verdict(deterministic: list[str], judge: dict, pipeline_unavailable: bool) -> str:
    if pipeline_unavailable:
        return "pipeline_unavailable"
    if any(v in ("fail", "error") for v in deterministic):
        return "fail"
    if judge["status"] == "not_judged":
        return "pass"
    return judge["verdict"] if judge["status"] == "judged" else judge["status"]


# --- judge agent and runner ---------------------------------------------------------------------------

def judge_key(settings: Settings, sample: int) -> dict:
    return {"model": settings.judge_model_id, "max_completion_tokens": settings.judge_max_tokens,
            "seed": settings.judge_seed + sample, "reasoning_effort": None,
            "temperature": settings.judge_temperature, "rubric_version": RUBRIC_VERSION, "sample": sample}


class JudgeModels(ModelFactory):
    """One ReplayModel per judge sample, under replay/judge/<case>/, keyed on the judge's own settings."""

    def __init__(self):
        super().__init__(namespace="judge")

    def live_model(self, settings: Settings) -> LiveModel:
        return build_judge_model(settings)

    def __call__(self, settings: Settings, store: Store, case_id: str, sample: int = 0) -> ReplayModel:
        return ReplayModel(self.live_model(settings), settings, store, case_id, namespace=self.namespace,
                           replay_files=self.replay_files, key_extras=judge_key(settings, sample))


def build_judge(model: ReplayModel, settings: Settings) -> Agent[None, JudgeVerdict]:
    agent = Agent(model, output_type=ToolOutput(JudgeVerdict, name=VERDICT_TOOL, strict=True, max_retries=1,
                                                description="Submit exactly one verdict per criterion."),
                  instructions=settings.judge_prompt_path.read_text(encoding="utf-8"), retries={"output": 1})

    @agent.output_validator
    def one_per_criterion(output: JudgeVerdict) -> JudgeVerdict:
        if sorted(v.criterion for v in output.verdicts) != sorted(CRITERIA):
            raise ModelRetry(f"Give exactly one verdict for each criterion: {', '.join(CRITERIA)}.")
        return output

    return agent


@dataclass
class JudgeRunner:
    settings: Settings
    store: Store
    catalog: Catalog
    models: JudgeModels
    samples: int = 3
    outcomes: dict[str, dict] = field(default_factory=dict)

    async def _sample(self, case: CaseData, prompt: list, sample: int) -> tuple[str, object]:
        agent = build_judge(self.models(self.settings, self.store, case.case_id, sample), self.settings)
        try:
            # one schema retry at most: a third request is over the limit
            result = await agent.run(prompt, model_settings={"seed": self.settings.judge_seed + sample},
                                     usage_limits=UsageLimits(request_limit=2))
        except LLMError as exc:
            return "judge_unavailable", f"{exc.code}: {exc}"
        except (UnexpectedModelBehavior, UsageLimitExceeded) as exc:
            return "judge_error", f"{type(exc).__name__}: {exc}"
        return "ok", result.output

    async def judge(self, case: CaseData) -> dict:
        if case.pipeline_unavailable:
            return {"status": "pipeline_unavailable"}
        if case.model_proposal is None:
            return {"status": "not_judged"}
        prompt = judge_prompt(case, self.catalog, self.settings.root)
        sources = [prompt[0], case.body, case.model_proposal.get("clarification") or ""]
        samples = []
        for i in range(self.samples):
            status, value = await self._sample(case, prompt, i)
            if status != "ok":
                return {"status": status, "error": value}
            samples.append(verify(value.verdicts, sources, bool(case.attachment_path)))
        criteria = {}
        agree = pairs = 0
        for name in CRITERIA:
            values = [s[name]["verdict"] for s in samples]
            verdict = majority(values)
            a, p = sample_agreement(values)
            agree, pairs = agree + a, pairs + p
            pick = next((s[name] for s in samples if s[name]["verdict"] == verdict), samples[0][name])
            criteria[name] = {"verdict": verdict, "samples": values, "evidence_quote": pick["evidence_quote"],
                              "rationale": pick["rationale"],
                              "flags": sorted({f for s in samples for f in s[name]["flags"]})}
        return {"status": "judged", "verdict": judge_only_verdict([c["verdict"] for c in criteria.values()]),
                "criteria": criteria, "agreement": [agree, pairs], "judge_model_id": self.settings.judge_model_id,
                "rubric_version": RUBRIC_VERSION}


@dataclass(repr=False)
class RubricJudge(Evaluator):
    """pydantic-evals adapter: per-criterion labels; the full outcome stays on the runner for the report."""

    runner: JudgeRunner

    def build_serialization_arguments(self) -> dict:
        return {"rubric_version": RUBRIC_VERSION, "samples": self.runner.samples}

    async def evaluate(self, ctx: EvaluatorContext) -> dict:
        outcome = await self.runner.judge(ctx.output)
        self.runner.outcomes[ctx.output.case_id] = outcome
        labels = {"judge": EvaluationReason(outcome.get("verdict", outcome["status"]), outcome.get("error"))}
        for name, c in outcome.get("criteria", {}).items():
            labels[f"judge.{name}"] = EvaluationReason(c["verdict"], c["evidence_quote"])
        return labels


# --- suite --------------------------------------------------------------------------------------------

def process_corpus(settings: Settings, store: Store, model_factory, catalog: Catalog) -> list[dict]:
    processing = Pipeline(settings, store, catalog=catalog, model_factory=model_factory).process_all()
    if settings.judge_cases_dir.is_dir():
        extra = replace(settings, requests_dir=settings.judge_cases_dir)
        processing += Pipeline(extra, store, catalog=catalog, model_factory=model_factory).process_all()
    return processing


def dataset_cases(cases: list[CaseData], defects: list[tuple[CaseData, str]], expected: dict) -> list[Case]:
    out = []
    for case in sorted(cases, key=lambda c: (c.request_id not in expected, id_key(c.case_id))):
        exp = expected.get(case.request_id)
        label = "pass" if exp is not None and case.model_proposal else None
        out.append(Case(name=case.case_id, inputs=case, expected_output=exp,
                        metadata={"kind": "reference" if exp is not None else "unlabelled", "label": label,
                                  "defect_class": None}))
    for case, defect_class in defects:
        out.append(Case(name=case.case_id, inputs=case, expected_output=expected.get(case.request_id),
                        metadata={"kind": "defect", "label": "fail", "defect_class": defect_class}))
    return out


def _label(report_case, name: str) -> dict:
    result = report_case.labels.get(name)
    if result is None:
        errors = "; ".join(f.error_message for f in report_case.evaluator_failures)
        return {"verdict": "error", "detail": errors or "missing"}
    return {"verdict": result.value, "detail": result.reason}


def case_row(report_case, outcome: dict | None) -> dict:
    case: CaseData = report_case.output
    meta = report_case.metadata
    trajectory, reference = _label(report_case, "trajectory"), _label(report_case, "reference")
    judge = outcome or {"status": "judge_error", "error": _label(report_case, "judge")["detail"]}
    return {"case_id": case.case_id, "request_id": case.request_id, "kind": meta["kind"], "label": meta["label"],
            "defect_class": meta["defect_class"], "image": bool(case.attachment_path),
            "status": case.observed["request"].get("status"), "trajectory": trajectory, "reference": reference,
            "judge": judge, "judge_only": judge.get("verdict", judge["status"]),
            "overall": overall_verdict([trajectory["verdict"], reference["verdict"]], judge,
                                       case.pipeline_unavailable)}


def envelope(settings: Settings) -> dict:
    return {"judge_model_id": settings.judge_model_id, "rubric_version": RUBRIC_VERSION,
            "corpus_fingerprint": report.corpus_fingerprint(settings)}


def run_judge(settings: Settings, out_dir: Path, *, judge_mode: str = "replay", pipeline_mode: str = "replay",
              samples: int = 3, update_baseline: bool = False, pipeline_factory=ModelFactory,
              judge_models=JudgeModels) -> dict:
    if samples < 1:
        raise ValueError(f"samples must be at least 1, got {samples}")
    check_judge_model(settings)
    ref = json.loads((settings.data_dir / "reference" / "expected.json").read_text(encoding="utf-8"))
    expected = {c["request_id"]: c["expected"] for c in ref["cases"]}
    catalog = Catalog.load(settings.catalog_path)
    drift_dir = Path(tempfile.mkdtemp(prefix="order-intake-drift-")) if pipeline_mode == "live" else None
    work = Path(tempfile.mkdtemp(prefix="order-intake-judge-"))
    try:
        pipe = replace(settings, db_path=work / "pipeline.db", llm_mode=pipeline_mode,
                       replay_dir=drift_dir or settings.replay_dir)
        store = Store(pipe.db_path)
        judge_store = Store(work / "judge-calls.db")
        for db in (store, judge_store):
            db.conn.execute("PRAGMA synchronous = OFF")  # throwaway databases: skip fsync on every commit
        try:
            processing = process_corpus(pipe, store, pipeline_factory(), catalog)
            cases = [snapshot(store, req) for req in store.list_requests()]
            defects = seed_defects({c.case_id: c for c in cases}, catalog)
            runner = JudgeRunner(replace(settings, llm_mode=judge_mode, db_path=judge_store.db_path), judge_store,
                                 catalog, judge_models(), samples)
            dataset = Dataset(name="order-intake-judge", cases=dataset_cases(cases, defects, expected),
                              evaluators=[TrajectoryEvaluator(cap=settings.max_steps), ReferenceEvaluator(),
                                          RubricJudge(runner)])
            evaluation = dataset.evaluate_sync(lambda case: case, task_name="pipeline_result", max_concurrency=1,
                                               progress=False)
            judge_calls = judge_store.conn.execute("SELECT COUNT(*) FROM llm_calls").fetchone()[0]
        finally:
            store.close()
            judge_store.close()
    finally:
        shutil.rmtree(work, ignore_errors=True)

    order = {c.name: i for i, c in enumerate(dataset.cases)}
    rows = [case_row(rc, runner.outcomes.get(rc.name)) for rc in sorted(evaluation.cases, key=lambda c: order[c.name])]
    env = envelope(settings)
    baseline_path = out_dir / "judge-baseline.json"
    previous = json.loads(baseline_path.read_text(encoding="utf-8")) if baseline_path.is_file() else None
    missing = [r["case_id"] for r in rows if r["judge"]["status"] in report.UNAVAILABLE]
    results = {
        "generated_at": now(), "envelope": env, "model_id": settings.model_id, "judge_mode": judge_mode,
        "pipeline_mode": pipeline_mode, "samples": samples, "criteria": list(CRITERIA),
        "drift_replay_dir": str(drift_dir) if drift_dir else None, "judge_model_calls": judge_calls,
        "processing": processing, "cases": rows, "calibration": report.calibration(rows),
        "noise_floor": report.noise_floor(rows), "baseline": report.compare_baseline(previous, rows, env),
        "missing_recordings": missing, "exit_code": 1 if missing else 0,
    }
    if update_baseline:
        if missing:
            results["baseline"]["update"] = f"not updated: {len(missing)} cases have no recording"
        else:
            out_dir.mkdir(parents=True, exist_ok=True)
            baseline_path.write_text(json.dumps(report.baseline_from(rows, env, samples), indent=2) + "\n",
                                     encoding="utf-8")
            results["baseline"]["update"] = f"written to {baseline_path.name}"
    report.write_reports(results, out_dir)
    return results
