import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]

LLM_MODES = ("live", "replay", "auto")


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
    api_key: str | None = field(default=None, repr=False)
    base_url: str = "https://openrouter.ai/api/v1"
    reasoning_effort: str = "low"
    max_completion_tokens: int = 4000
    seed: int = 7
    max_steps: int = 6
    timeout_s: float = 60.0


def load_settings(**overrides) -> Settings:
    load_dotenv(ROOT / ".env")
    env = {
        "model_id": os.getenv("MODEL_ID"),
        "llm_mode": os.getenv("LLM_MODE"),
        "api_key": os.getenv("OPENROUTER_API_KEY") or None,
        "reasoning_effort": os.getenv("MODEL_REASONING_EFFORT"),
        "db_path": Path(os.environ["DB_PATH"]) if os.getenv("DB_PATH") else None,
    }
    values = {k: v for k, v in env.items() if v is not None}
    values.update({k: v for k, v in overrides.items() if v is not None})
    if "db_path" in values and not Path(values["db_path"]).is_absolute():
        values["db_path"] = ROOT / values["db_path"]
    settings = Settings(**values)
    if settings.llm_mode not in LLM_MODES:
        raise ValueError(f"LLM_MODE must be one of {LLM_MODES}, got {settings.llm_mode!r}")
    return settings
