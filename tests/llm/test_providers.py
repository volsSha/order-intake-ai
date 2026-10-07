from dataclasses import replace

import pytest
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.models.openrouter import OpenRouterModel

from order_intake.config import ConfigError
from order_intake.llm.providers import APP_TITLE, NO_KEY, build_model

pytestmark = pytest.mark.unit


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


def test_openrouter_model_carries_reasoning_effort_seed_and_limits(settings):
    live = build_model(replace(settings, openrouter_api_key="or-key"))
    assert live.provider == "openrouter" and live.problem is None
    assert isinstance(live.model, OpenRouterModel) and live.model.model_name == "openai/gpt-6-luna"
    assert live.model.settings == {"openrouter_reasoning": {"effort": "low"}, "seed": 7, "max_tokens": 4000,
                                   "timeout": 60.0}
    client = live.model.client
    assert (client.max_retries, client.timeout, str(client.base_url)) == (1, 60.0, "https://openrouter.ai/api/v1/")
    assert client.default_headers["X-Title"] == APP_TITLE


def test_openai_fallback_strips_the_prefix_and_uses_openai_settings(settings):
    live = build_model(replace(settings, openai_api_key="oa-key", reasoning_effort="medium"))
    assert live.provider == "openai"
    assert type(live.model) is OpenAIChatModel and live.model.model_name == "gpt-6-luna"
    assert live.model.settings == {"openai_reasoning_effort": "medium", "seed": 7, "max_tokens": 4000,
                                   "timeout": 60.0}
    assert live.model.client.max_retries == 1 and "X-Title" not in live.model.client.default_headers


def test_no_key_builds_a_never_called_openrouter_model(settings):
    live = build_model(settings)
    assert live.provider is None and live.problem == NO_KEY
    assert isinstance(live.model, OpenRouterModel)


def test_non_openai_model_without_override_is_a_config_problem(settings):
    live = build_model(replace(settings, openai_api_key="oa-key", model_id="anthropic/claude-sonnet-5.5"))
    assert live.provider is None and "OPENAI_MODEL_ID" in live.problem
