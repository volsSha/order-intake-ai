# Order intake take-home — agent instructions

Project for the Junior AI Engineer take-home, alternative A (order intake & exception handling).

## Source of truth
- `starter/orders/domain.md` defines the business rules. Never add industry rules (pack sizes, taxes, shipping, other discounts).
- Files under `starter/` are the unmodified starter pack (checksums in `starter/SHA256SUMS`). Do not edit them.
- Expected results in `data/reference/` are written and checked independently of application code. Never generate them from application output.

## Architecture rules
- The model only interprets text and proposes SKUs/quantities. Prices, totals, discounts, duplicates and statuses are computed in code.
- A proposed SKU is accepted only if it was returned by the `search_catalog` tool in the same conversation.
- Money is integer USD cents. 10% line discount for >= 10 items, rounded half up.
- Every real model call is recorded under `replay/` so the app runs without an API key (`LLM_MODE=replay`).

## Workflow
- Stack: Python 3.12, uv, FastAPI + Jinja2 + HTMX, SQLite, pytest, ruff.
- Run `uv run pytest` and `uv run ruff check` before finishing a change.
- Keep comments rare and short. No secrets in the repo; env var names go in `.env.example`.
- Log notable AI-assisted steps (instruction, check, correction) in `docs/DEV_JOURNAL.md`.
