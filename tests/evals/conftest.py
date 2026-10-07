from dataclasses import replace

import pytest
from pydantic_ai.messages import ModelResponse, ToolCallPart, UserPromptPart
from pydantic_ai.models.function import FunctionModel

from order_intake.evals.judge import CRITERIA, JudgeModels, run_judge
from order_intake.llm.providers import LiveModel
from tests.conftest import SCRIPTED, ScriptedModels, line

J1 = ("Request-ID: J1\nOrder-Ref: JO1\nFrom: a@example.test\nSubject: Cables\n\n"
      "Could we get a dozen of the two-metre USB-C cables?\n")
SCRIPT = {**SCRIPTED, "J1": [line("two-metre USB-C cables", "CAB-2", "a dozen", 12)]}


def user_text(messages) -> str:
    part = next(p for m in messages for p in m.parts if isinstance(p, UserPromptPart))
    return part.content if isinstance(part.content, str) else part.content[0]


def request_of(text: str) -> str:
    return text.split("<request>\n", 1)[1].split("\n</request>", 1)[0]


def verdicts(quote: str, default: str = "pass", **overrides) -> dict:
    return {"verdicts": [{"criterion": c, "verdict": overrides.get(c, default), "evidence_quote": quote,
                          "rationale": "because"} for c in CRITERIA]}


def honest(text: str) -> dict:
    """Passes everything, quoting the first request line verbatim."""
    return verdicts(request_of(text).splitlines()[0])


class FakeJudges(JudgeModels):
    """FunctionModel judge behind the real ReplayModel; decide(prompt_text) returns the verdict arguments."""

    def __init__(self, decide=honest):
        super().__init__()
        self.decide = decide
        self.messages: list = []

    def respond(self, messages, info) -> ModelResponse:
        self.messages.append(messages)
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, self.decide(user_text(messages)))])

    def live_model(self, settings):
        return LiveModel(FunctionModel(self.respond), "scripted")


@pytest.fixture
def judge_settings(settings, tmp_path):
    cases = tmp_path / "judge-cases"
    cases.mkdir()
    (cases / "J1.txt").write_text(J1, encoding="utf-8")
    return replace(settings, judge_cases_dir=cases)


@pytest.fixture
def judge_run(judge_settings, tmp_path):
    """Runs the suite; the scripted pipeline records on the first call, later calls replay it."""
    recorded = []

    def run(judges=None, *, mode="live", samples=1, out=None, settings=None, **kw):
        pipeline = (lambda: ScriptedModels(script=SCRIPT)) if not recorded else None
        recorded.append(True)
        extra = {"pipeline_factory": pipeline} if pipeline else {}
        return run_judge(settings or judge_settings, out or tmp_path / "out", judge_mode=mode, samples=samples,
                         judge_models=(lambda: judges) if judges else JudgeModels, **extra, **kw)

    return run
