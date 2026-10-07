import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]

LLM_MODES = ("live", "replay", "auto")
PROVIDERS = ("auto", "openrouter", "openai")


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Provider:
    name: str
    base_url: str
    api_key: str | None = field(repr=False)
    model: str


@dataclass(frozen=True)
class Settings:
    root: Path = ROOT
    data_dir: Path = ROOT / "data"
    requests_dir: Path = ROOT / "data" / "requests"
    catalog_path: Path = ROOT / "data" / "catalog.json"
    prompt_path: Path = ROOT / "prompts" / "extract_order.md"
    replay_dir: Path = ROOT / "replay"
    db_path: Path = ROOT / "var" / "intake.db"
    model_id: str = "openai/gpt-6-luna"
    llm_mode: str = "replay"
    provider: str = "auto"
    openrouter_api_key: str | None = field(default=None, repr=False)
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openai_api_key: str | None = field(default=None, repr=False)
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model_id: str | None = None
    reasoning_effort: str = "low"
    max_completion_tokens: int = 4000
    seed: int = 7
    max_steps: int = 6
    timeout_s: float = 60.0

    def resolve_provider(self) -> Provider | None:
        """OpenRouter when its key is set, otherwise OpenAI directly; None when no key is available."""
        name = self.provider
        if name == "auto":
            name = "openrouter" if self.openrouter_api_key else "openai" if self.openai_api_key else None
        if name is None:
            return None
        if name == "openrouter":
            return Provider("openrouter", self.openrouter_base_url, self.openrouter_api_key, self.model_id)
        model = self.openai_model_id
        if not model:
            if not self.model_id.startswith("openai/"):
                raise ConfigError(f"MODEL_ID {self.model_id!r} is not an OpenAI model; set OPENAI_MODEL_ID "
                                  "to use the OpenAI fallback.")
            model = self.model_id.removeprefix("openai/")
        return Provider("openai", self.openai_base_url, self.openai_api_key, model)


def load_settings(**overrides) -> Settings:
    load_dotenv(ROOT / ".env")
    env = {
        "model_id": os.getenv("MODEL_ID"),
        "llm_mode": os.getenv("LLM_MODE"),
        "provider": os.getenv("LLM_PROVIDER"),
        "openrouter_api_key": os.getenv("OPENROUTER_API_KEY") or None,
        "openai_api_key": os.getenv("OPENAI_API_KEY") or None,
        "openai_model_id": os.getenv("OPENAI_MODEL_ID") or None,
        "reasoning_effort": os.getenv("MODEL_REASONING_EFFORT"),
        "db_path": Path(os.environ["DB_PATH"]) if os.getenv("DB_PATH") else None,
    }
    values = {k: v for k, v in env.items() if v}
    values.update({k: v for k, v in overrides.items() if v is not None})
    if "db_path" in values and not Path(values["db_path"]).is_absolute():
        values["db_path"] = ROOT / values["db_path"]
    settings = Settings(**values)
    if settings.llm_mode not in LLM_MODES:
        raise ConfigError(f"LLM_MODE must be one of {LLM_MODES}, got {settings.llm_mode!r}")
    if settings.provider not in PROVIDERS:
        raise ConfigError(f"LLM_PROVIDER must be one of {PROVIDERS}, got {settings.provider!r}")
    return settings
