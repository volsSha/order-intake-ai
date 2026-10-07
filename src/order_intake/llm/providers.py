"""Real chat models: OpenRouter when its key is set, otherwise OpenAI directly, otherwise replay only."""

from dataclasses import dataclass

from openai import AsyncOpenAI
from pydantic_ai.models import Model
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIChatModelSettings
from pydantic_ai.models.openrouter import OpenRouterModel, OpenRouterModelSettings
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.providers.openrouter import OpenRouterProvider

from ..config import ConfigError, Provider, Settings

APP_TITLE = "order-intake take-home"
PLACEHOLDER_KEY = "replay-only-no-key"
NO_KEY = ("No API key: set OPENROUTER_API_KEY (or OPENAI_API_KEY as a fallback) in .env, "
          "or use LLM_MODE=replay.")
NO_JUDGE_KEY = "The live judge needs OPENROUTER_API_KEY; without it the judge is replay-only."


@dataclass
class LiveModel:
    model: Model
    provider: str | None  # None: no usable provider, the model is never called
    problem: str | None = None


def model_settings(settings: Settings, provider: str) -> OpenRouterModelSettings | OpenAIChatModelSettings:
    common = {"seed": settings.seed, "max_tokens": settings.max_completion_tokens, "timeout": settings.timeout_s}
    if provider == "openrouter":
        return OpenRouterModelSettings(openrouter_reasoning={"effort": settings.reasoning_effort}, **common)
    return OpenAIChatModelSettings(openai_reasoning_effort=settings.reasoning_effort, **common)


def _client(settings: Settings, base_url: str, api_key: str, headers: dict | None = None) -> AsyncOpenAI:
    return AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=settings.timeout_s, max_retries=1,
                       default_headers=headers)


def _openrouter(settings: Settings, api_key: str, model_id: str | None = None,
                model_config: OpenRouterModelSettings | None = None) -> Model:
    client = _client(settings, settings.openrouter_base_url, api_key, {"X-Title": APP_TITLE})
    return OpenRouterModel(model_id or settings.model_id, provider=OpenRouterProvider(openai_client=client),
                           settings=model_config or model_settings(settings, "openrouter"))


def _openai(settings: Settings, provider: Provider) -> Model:
    client = _client(settings, provider.base_url, provider.api_key)
    return OpenAIChatModel(provider.model, provider=OpenAIProvider(openai_client=client),
                           settings=model_settings(settings, "openai"))


def build_model(settings: Settings) -> LiveModel:
    try:
        provider = settings.resolve_provider()
    except ConfigError as exc:
        return LiveModel(_openrouter(settings, PLACEHOLDER_KEY), None, str(exc))
    if provider is None or not provider.api_key:
        return LiveModel(_openrouter(settings, PLACEHOLDER_KEY), None, NO_KEY)
    if provider.name == "openrouter":
        return LiveModel(_openrouter(settings, provider.api_key), "openrouter")
    return LiveModel(_openai(settings, provider), "openai")


def judge_model_settings(settings: Settings) -> OpenRouterModelSettings:
    return OpenRouterModelSettings(seed=settings.judge_seed, max_tokens=settings.judge_max_tokens,
                                   temperature=settings.judge_temperature, timeout=settings.timeout_s)


def build_judge_model(settings: Settings) -> LiveModel:
    """OpenRouter only: an OpenAI-hosted judge would share the pipeline's model family."""
    key = settings.openrouter_api_key
    model = _openrouter(settings, key or PLACEHOLDER_KEY, settings.judge_model_id, judge_model_settings(settings))
    return LiveModel(model, "openrouter", None) if key else LiveModel(model, None, NO_JUDGE_KEY)
