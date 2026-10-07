# Order intake take-home — agent instructions

Project for the Junior AI Engineer take-home, alternative A (order intake & exception handling).

## Source of truth
- `starter/orders/domain.md` defines the business rules. Never add industry rules (pack sizes, taxes, shipping, other discounts).
- Files under `starter/` are the unmodified starter pack (checksums in `starter/SHA256SUMS`). Do not edit them.
- Expected results in `data/reference/` are written and checked independently of application code. Never generate them from application output.

## Architecture rules
- Layout: `domain/` (rules, no model), `llm/` (PydanticAI agent, replay wrapper, providers), `evals/` (check, judge), `web/`. See `src/order_intake/README.md`.
- The model only interprets text and proposes SKUs/quantities. Prices, totals, discounts, duplicates and statuses are computed in code.
- A proposed SKU is accepted only if it was returned by the `search_catalog` tool in the same run. Business rules live in `domain/validation.py`, never only in a prompt.
- Money is integer USD cents. 10% line discount for >= 10 items, rounded half up.
- Every real model call is recorded under `replay/` so the app, `check` and `judge` run without an API key (`LLM_MODE=replay`). A prompt or setting change means re-recording.
- The LLM judge is an offline evaluation tool from another model family; it never changes an order.

## Workflow
- Stack: Python 3.12, uv, PydanticAI + pydantic-evals, FastAPI + Jinja2 + HTMX, SQLite, pytest + Hypothesis, ruff.
- Run `uv run pytest --cov` and `uv run ruff check .` before finishing a change. For key-less runs set `OPENROUTER_API_KEY= OPENAI_API_KEY=` (empty), because `.env` is loaded otherwise.
- Keep comments rare and short. No secrets in the repo; env var names go in `.env.example`.
- Log notable AI-assisted steps (instruction, check, correction) in `docs/DEV_JOURNAL.md`.
