# Tests

```bash
uv run pytest                 # everything; no network, no API key needed
uv run pytest --cov           # with the coverage gate (90% on deterministic code)
uv run pytest -m unit         # fast rule tests only
uv run pytest -m integration  # pipeline, web, replay-backed runs
CI=1 uv run pytest            # derandomized Hypothesis profile, as in CI
```

Real model requests are blocked for the whole suite (`pydantic_ai.models.ALLOW_MODEL_REQUESTS = False`). Model behaviour comes from scripted `FunctionModel` fixtures, or from the recordings in [`replay/`](../replay/).

| Path | What it proves |
|---|---|
| [`conftest.py`](conftest.py) | Shared fixtures: settings on a temporary DB, `ScriptedModels` (the scripted pipeline model), Hypothesis profiles |
| [`domain/`](domain/) | Business rules: pricing boundaries and property-based invariants, catalog matching, request parsing, every validation finding, clarification wording |
| [`llm/`](llm/) | PydanticAI agent limits and error codes, replay key stability, record/replay modes, simulated failures, provider settings |
| [`evals/`](evals/) | Trajectory checks, reference adapter, judge quote verification, majority and noise floor, calibration math, baseline comparison, blindness of the judge prompt |
| [`web/`](web/) | Queue filter, detail page, correction → approval → export through HTTP forms, error redirects, model-call table |
| [`test_pipeline.py`](test_pipeline.py) | End-to-end batch: statuses, duplicates, conflicts, idempotent reprocessing, simulated failures, the `check` runner |
| [`test_storage.py`](test_storage.py) | Non-destructive schema upgrade of an existing database |
| [`test_repo_hygiene.py`](test_repo_hygiene.py) | Secret scan of every tracked file (key patterns and the literal values from a local `.env`), `.env` not tracked, replay files valid with no orphans, starter-pack checksums |
