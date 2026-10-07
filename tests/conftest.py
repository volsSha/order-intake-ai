import os
from dataclasses import replace

import pydantic_ai.models
import pytest
from hypothesis import settings as hypothesis_settings
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from order_intake.config import load_settings
from order_intake.llm.providers import LiveModel
from order_intake.llm.replay import ModelFactory, ReplayModel
from order_intake.pipeline import Pipeline
from order_intake.storage import Store

pydantic_ai.models.ALLOW_MODEL_REQUESTS = False

hypothesis_settings.register_profile("ci", derandomize=True, deadline=None, print_blob=True)
hypothesis_settings.register_profile("dev", max_examples=100)
hypothesis_settings.load_profile("ci" if os.environ.get("CI") else "dev")


def line(product_text, sku, qty_text, qty, product_status="matched", quantity_status="explicit", unit="item",
         candidates=None):
    return {"product_text": product_text, "product_status": product_status, "sku": sku,
            "candidate_skus": candidates or ([sku] if sku else []), "quantity_text": qty_text,
            "quantity_status": quantity_status, "quantity": qty, "unit": unit}


# What a well-behaved model is expected to submit for each request (used only by unit tests).
SCRIPTED = {
    "R1": [line("CAB-1 cables", "CAB-1", "2", 2)],
    "R2": [line("Moon adapter", None, "one", 1, product_status="unknown")],
    "R3": [line("the usual cable", None, "two boxes", None, product_status="ambiguous",
                quantity_status="ambiguous", unit="container", candidates=["CAB-1", "CAB-2"])],
    "R5": [line("USB hubs", "HUB-1", "12", 12)],
    "R6": [line("HUB-1", "HUB-1", "3 x", 3), line("USB-C cable 2 m", "CAB-2", "10 x", 10)],
    "R7": [line("USB-C cables", None, "10", 10, product_status="ambiguous", candidates=["CAB-1", "CAB-2"])],
    "R8": [line("USB hubs", "HUB-1", "a few", None, quantity_status="ambiguous", unit="unclear")],
    "R10": [line("CAB-2 cables", "CAB-2", "2", 2)],
    "R11": [line("CAB-2 USB-C cable 2 m", "CAB-2", "4", 4)],
}


class ScriptedModels(ModelFactory):
    """Scripted stand-in for the model behind the real ReplayModel: step 1 searches, step 2 submits."""

    def __init__(self, script=None, simulate=None):
        super().__init__(simulate=simulate)
        self.script = script if script is not None else SCRIPTED
        self.calls = 0

    def respond(self, request_id: str, messages, info: AgentInfo) -> ModelResponse:
        self.calls += 1
        lines = self.script[request_id]
        if len(messages) == 1:
            return ModelResponse(parts=[ToolCallPart("search_catalog", {"query": ln["product_text"]}) for ln in lines])
        args = {"lines": lines, "clarification_draft": None, "notes": ""}
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, args)])

    def __call__(self, settings, store, request_id):
        model = FunctionModel(lambda messages, info: self.respond(request_id, messages, info))
        return ReplayModel(LiveModel(model, "scripted"), replace(settings, llm_mode="live"), store, request_id,
                           simulate=self.simulate.get(request_id), replay_files=self.replay_files)


@pytest.fixture
def settings(tmp_path):
    return replace(load_settings(llm_mode="replay"), db_path=tmp_path / "test.db", replay_dir=tmp_path / "replay",
                   provider="auto", openrouter_api_key=None, openai_api_key=None, openai_model_id=None)


@pytest.fixture
def make_pipeline(settings):
    def factory(script=None, simulate=None):
        store = Store(settings.db_path)
        return Pipeline(settings, store, model_factory=ScriptedModels(script=script, simulate=simulate))

    return factory
