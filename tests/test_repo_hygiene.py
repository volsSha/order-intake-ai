import hashlib
import json
import re
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest
from dotenv import dotenv_values

from order_intake.config import ROOT, load_settings
from order_intake.evals.check import run_checks
from order_intake.llm.replay import ModelFactory

REPLAY_KEYS = {"format_version", "replay_key", "request_id", "step", "recorded_at", "provider", "provider_model",
               "latency_ms", "request", "response"}
REPLAY_NAMESPACES = {"extract", "judge"}

KEY_PATTERNS = {
    "sk-key": re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"),
    "bearer-token": re.compile(r"authorization[\"']?\s*[:=]\s*[\"']?bearer\s+[A-Za-z0-9._~+/=-]{20,}",
                               re.IGNORECASE),
}


def find_key_shapes(text: str) -> list[str]:
    """Names of the key-shaped patterns found in text; never the matched values."""
    return [name for name, pattern in KEY_PATTERNS.items() if pattern.search(text)]


def read_text(path: Path) -> str | None:
    data = path.read_bytes()
    if b"\0" in data:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def tracked_files() -> list[Path]:
    if not (ROOT / ".git").exists():
        pytest.skip("not a git checkout")
    out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True).stdout
    return [ROOT / name for name in out.decode().split("\0") if name and (ROOT / name).is_file()]


def files_with_literal_values(files: list[Path], env_path: Path) -> list[str]:
    values = [v for k, v in dotenv_values(env_path).items() if k.endswith("_API_KEY") and v]
    offenders = []
    for path in files:
        text = read_text(path)
        if text is not None and any(v in text for v in values):
            offenders.append(str(path.relative_to(ROOT)))
    return offenders


def checksum_mismatches(sums_file: Path) -> list[str]:
    # SHA256SUMS paths come from the original pack, where task folders sit under tasks/
    root = sums_file.parent
    bad = []
    for row in sums_file.read_text(encoding="utf-8").splitlines():
        if not row.strip():
            continue
        digest, name = row.split(maxsplit=1)
        path = root / name.removeprefix("tasks/")
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            bad.append(name)
    return bad


@pytest.mark.unit
@pytest.mark.parametrize("text", [
    "OPENROUTER_API_KEY=" + "sk-or-v1-" + "a1B2" * 8,
    "key = 'sk-" + "proj_" + "x" * 30 + "'",
    "Authorization: Bearer " + "abcDEF123" * 3,
    '"authorization": "bearer ' + "t0k3n" * 5 + '"',
])
def test_scanner_detects_planted_key(tmp_path: Path, text):
    planted = tmp_path / "planted.txt"
    planted.write_text(f"some config\n{text}\nmore\n")
    assert find_key_shapes(planted.read_text())


@pytest.mark.unit
@pytest.mark.parametrize("text", [
    "The client sends an Authorization header with a Bearer token from the environment.",
    'headers = {"Authorization": f"Bearer {api_key}"}',
    "Keys look like sk-... and are never committed.",
    "The task-management-and-tracking-board is out of scope.",
])
def test_scanner_ignores_prose(text):
    assert find_key_shapes(text) == []


@pytest.mark.integration
def test_no_key_shaped_strings_in_tracked_files():
    offenders = []
    for path in tracked_files():
        text = read_text(path)
        if text is not None and find_key_shapes(text):
            offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []


@pytest.mark.integration
def test_local_env_key_values_do_not_appear_in_tracked_files():
    env_path = ROOT / ".env"
    if not env_path.is_file():
        pytest.skip("no local .env")
    assert files_with_literal_values(tracked_files(), env_path) == []


@pytest.mark.integration
def test_env_file_is_not_tracked():
    tracked_files()
    result = subprocess.run(["git", "ls-files", "--error-unmatch", ".env"], cwd=ROOT, capture_output=True,
                            check=False)
    assert result.returncode != 0


@pytest.mark.integration
def test_replay_files_parse_and_have_the_recorded_keys():
    files = sorted((ROOT / "replay").rglob("*.json"))
    assert files, "no replay recordings in the repository"
    for path in files:
        record = json.loads(path.read_text(encoding="utf-8"))
        name = str(path.relative_to(ROOT))
        assert isinstance(record, dict), name
        assert path.relative_to(ROOT / "replay").parts[0] in REPLAY_NAMESPACES, name
        assert REPLAY_KEYS <= record.keys(), name
        assert record["format_version"] == 2, name
        assert isinstance(record["request"], dict) and isinstance(record["response"], dict), name
        assert isinstance(record["step"], int) and record["step"] >= 1, name


@pytest.mark.integration
def test_starter_pack_matches_checksums():
    assert checksum_mismatches(ROOT / "starter" / "SHA256SUMS") == []


@pytest.mark.unit
def test_modified_starter_file_fails_checksum(tmp_path: Path):
    (tmp_path / "orders").mkdir()
    (tmp_path / "orders" / "a.md").write_text("original")
    (tmp_path / "b.json").write_text("{}")
    digest = hashlib.sha256(b"original").hexdigest()
    (tmp_path / "SHA256SUMS").write_text(
        f"{digest}  tasks/orders/a.md\n{hashlib.sha256(b'{}').hexdigest()}  b.json\n")
    assert checksum_mismatches(tmp_path / "SHA256SUMS") == []
    (tmp_path / "orders" / "a.md").write_text("edited")
    assert checksum_mismatches(tmp_path / "SHA256SUMS") == ["tasks/orders/a.md"]


@pytest.mark.integration
def test_every_extract_recording_is_read_by_a_full_replay_run(tmp_path):
    factories: list[ModelFactory] = []

    def tracking_factory(**kwargs):
        factories.append(ModelFactory(**kwargs))
        return factories[-1]

    settings = replace(load_settings(llm_mode="replay"), db_path=tmp_path / "x.db", openrouter_api_key=None,
                       openai_api_key=None)
    assert run_checks(settings, tmp_path, model_factory=tracking_factory)["passed"]
    read = {f.resolve() for factory in factories for f in factory.replay_files}
    recorded = {f.resolve() for f in (ROOT / "replay" / "extract").rglob("*.json")}
    orphans = sorted(str(f.relative_to(ROOT)) for f in recorded - read)
    assert not orphans, f"replay files no run reads: {orphans}"
