import json
import os
from dataclasses import replace

import pytest
from hypothesis import settings as hypothesis_settings

from order_intake.config import load_settings
from order_intake.llm.llm import ChatClient, LLMResult
from order_intake.pipeline import Pipeline
from order_intake.storage import Store

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


class FakeClient(ChatClient):
    """Scripted stand-in for the model: step 1 searches, step 2 submits."""

    def __init__(self, settings, script=None, simulate=None):
        super().__init__(settings, simulate=simulate)
        self.script = script if script is not None else SCRIPTED
        self.calls = 0

    def complete(self, request_id, step, messages, tools):
        if request_id in self.simulate:
            return super().complete(request_id, step, messages, tools)
        self.calls += 1
        lines = self.script[request_id]
        record = {"call_id": f"{request_id}-fake-{step}", "request_id": request_id, "step": step,
                  "model": "fake", "source": "simulated"}
        if step == 1:
            calls = [{"id": f"c{i}", "type": "function",
                      "function": {"name": "search_catalog", "arguments": json.dumps({"query": ln["product_text"]})}}
                     for i, ln in enumerate(lines)]
        else:
            args = {"lines": lines, "clarification_draft": None, "notes": ""}
            calls = [{"id": "s1", "type": "function",
                      "function": {"name": "submit_order_draft", "arguments": json.dumps(args)}}]
        return LLMResult({"role": "assistant", "content": None, "tool_calls": calls}, record)


@pytest.fixture
def settings(tmp_path):
    return replace(load_settings(llm_mode="replay"), db_path=tmp_path / "test.db", replay_dir=tmp_path / "replay",
                   provider="auto", openrouter_api_key=None, openai_api_key=None, openai_model_id=None)


@pytest.fixture
def make_pipeline(settings):
    def factory(script=None, simulate=None):
        store = Store(settings.db_path)
        return Pipeline(settings, store, client=FakeClient(settings, script=script, simulate=simulate))

    return factory
