import json
from dataclasses import replace

import pytest

from order_intake.config import ConfigError
from order_intake.llm.llm import ChatClient, LLMError, ReplayMissing
from order_intake.llm.schemas import TOOLS

MESSAGES = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]


def test_openrouter_preferred_when_both_keys_present(settings):
    s = replace(settings, openrouter_api_key="or-key", openai_api_key="oa-key")
    assert s.resolve_provider().name == "openrouter"
    assert s.resolve_provider().model == "openai/gpt-6-luna"


def test_openai_fallback_without_openrouter_key(settings):
    p = replace(settings, openai_api_key="oa-key").resolve_provider()
    assert (p.name, p.base_url, p.model) == ("openai", "https://api.openai.com/v1", "gpt-6-luna")


def test_openai_fallback_needs_an_openai_model(settings):
    s = replace(settings, openai_api_key="oa-key", model_id="anthropic/claude-sonnet-5.5")
    with pytest.raises(ConfigError):
        s.resolve_provider()
    assert replace(s, openai_model_id="gpt-6-sol").resolve_provider().model == "gpt-6-sol"


def test_no_keys_means_no_provider(settings):
    assert settings.resolve_provider() is None


def test_provider_specific_parameters(settings):
    client = ChatClient(settings)
    msgs = MESSAGES + [{"role": "assistant", "content": None, "reasoning_details": [{"x": 1}]}]
    orp = replace(settings, openrouter_api_key="k").resolve_provider()
    oap = replace(settings, openai_api_key="k").resolve_provider()
    p_or = client.live_params(orp, msgs, TOOLS)
    p_oa = client.live_params(oap, msgs, TOOLS)
    assert p_or["extra_body"] == {"reasoning": {"effort": "low"}} and "reasoning_effort" not in p_or
    assert p_oa["reasoning_effort"] == "low" and "extra_body" not in p_oa
    assert "reasoning_details" not in p_oa["messages"][-1] and "reasoning_details" in p_or["messages"][-1]


def test_replay_key_is_provider_independent(settings):
    a = ChatClient(replace(settings, openrouter_api_key="k"))
    b = ChatClient(replace(settings, openai_api_key="k"))
    assert a._canonical(MESSAGES, TOOLS) == b._canonical(MESSAGES, TOOLS)


def test_live_without_any_key_fails_clearly(settings):
    client = ChatClient(replace(settings, llm_mode="live"))
    with pytest.raises(LLMError, match="No API key"):
        client.complete("R1", 1, MESSAGES, TOOLS)


def test_replay_returns_recorded_response_and_misses_on_changed_input(settings):
    client = ChatClient(settings)
    with pytest.raises(ReplayMissing) as miss:
        client.complete("R1", 1, MESSAGES, TOOLS)
    key = miss.value.args[1]["replay_key"]
    path = client.replay_path("R1", 1, key)
    path.parent.mkdir(parents=True)
    response = {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [
        {"id": "t1", "type": "function", "function": {"name": "search_catalog", "arguments": "{\"query\":\"x\"}"}}]}}],
        "usage": {"total_tokens": 10}}
    path.write_text(json.dumps({"provider": "openrouter", "response": response}))
    result = client.complete("R1", 1, MESSAGES, TOOLS)
    assert result.record["source"] == "replay" and result.record["provider"] == "openrouter"
    assert result.message["tool_calls"][0]["function"]["name"] == "search_catalog"
    with pytest.raises(ReplayMissing):
        client.complete("R1", 1, MESSAGES + [{"role": "user", "content": "changed"}], TOOLS)
