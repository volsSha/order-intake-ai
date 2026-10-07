# Development journal

Chronological record of how the solution was built with AI assistance: what was asked, how the result was checked, and what was corrected. Source for the LLM usage note (`docs/LLM_USAGE.md`).

## Stage 0 — Scaffold (2026-10-07)

**Goal.** Separate Git repository for alternative A, starter pack copied unchanged, tooling chosen.

**Decisions.**
- Stack: Python 3.12 + FastAPI + Jinja2 + HTMX + SQLite. One language, server-rendered review UI, easy to test with pytest. React/Streamlit/Laravel were considered; rejected for time (React), testability (Streamlit), and AI-ecosystem fit (Laravel).
- LLM layer: plain `openai` SDK pointed at OpenRouter with a hand-written tool loop. LangGraph/LangChain were considered and rejected: the flow is linear (lookup → submit), and raw request/response capture for replay is simpler without a framework.
- Model: `openai/gpt-6-luna` via OpenRouter (cheap, supports tools, structured outputs, image input). Configurable with `MODEL_ID`. No key was supplied by the hiring team; the candidate's own OpenRouter key with a spending cap is used.
- Starter files live in `starter/` with SHA-256 checksums so a reviewer can confirm they are unmodified.

**AI configuration introduced.** Project `CLAUDE.md` (agent instructions for Claude Code).

## Stage 1 — Data preparation (2026-10-07)

**Goal.** Extend the 4 seed requests to ~10, keep seed cases, write expected results independently of the app.

**AI instruction (Claude Code, following `starter/skills/generate-assignment-data/SKILL.md`).** "Extend tasks/orders to about 12 email-style requests. Keep R1–R4 bodies verbatim. Add one scenario per gap: bulk discount, per-line discount, reviewer correction ending at the worked example (10 × CAB-1 = 18000), vague quantity, same order ref with different content, a prompt-injection attempt on price, an image attachment, an unusable file. Do not add catalog items or pricing policies."

**Checks.**
- `scripts/verify_reference.py` re-implements the two pricing rules with `fractions.Fraction` and imports nothing from the app; it recomputes every expected line and total and the domain.md worked example. Result: OK.
- Mutation check of the checker itself: changed REF-6's CAB-2 discount to 0 → verifier reported 3 errors; restored → OK. This shows the verifier would catch a wrong expectation.
- Rendered attachment inspected visually.

**Correction / finding.** While writing a rounding case the catalog turned out to make rounding impossible to exercise (all prices are multiples of 1000, so 10% is always whole cents). Instead of adding catalog items (the brief says keep the catalog), rounding moved to unit tests with synthetic prices, and the limitation is documented in `data/GENERATION.md`.

## Stage 2 — Domain core (2026-10-07)

**Goal.** Deterministic parts first, so the model has something strict to be checked against: request parsing, catalog lookup, pricing, validation, SQLite storage.

**Design.** The model will only *propose* lines (product wording, SKU from the lookup, quantity wording). `validation.py` decides: SKU must exist and must have been returned by `search_catalog` in the same conversation; the product wording is re-matched against the catalog in code, so a model that quietly picks CAB-1 for "USB-C cables" is overridden to `AMBIGUOUS_PRODUCT`; the quantity must be written in the request; containers never become item counts. Prices come only from `pricing.py`.

**Checks.** 28 unit tests (`tests/test_core.py`) including the domain worked example, half-up rounding with synthetic prices, and one test per finding code.

**Correction.** The first catalog search treated "CAB-1 cables" as a description search, so CAB-1 and CAB-2 tied on the word "cable" — an explicit SKU would have been reported as ambiguous. Found by reading the generated search code against the seed wording before running anything; fixed by detecting SKU-shaped words inside the phrase before description matching, and locked with `test_search_by_sku_inside_phrase`.

## Stage 3 — Model integration and pipeline (2026-10-07)

**Goal.** Real model call inside the app, bounded and replayable; batch pipeline; reviewer actions.

**Design.**
- `prompts/extract_order.md` holds the system prompt (versioned in Git). Request text is wrapped in `<request>` and declared to be data, not instructions.
- Two tools only: `search_catalog` (the only product source) and `submit_order_draft` (strict JSON schema generated from Pydantic). `tool_choice=required`, at most 6 model calls, one retry after invalid arguments, then `failed / INVALID_MODEL_OUTPUT`.
- `llm.py` records every live call to `replay/<request>/stepNN-<sha256 prefix>.json`. The key hashes model + messages + tools + parameters, so replay can never return a response produced for a different prompt or input: a changed prompt is a visible `REPLAY_MISSING`, not a silently stale answer.
- Simulated failures (`--simulate R1=model_unavailable`, `invalid_output`) are labelled `source=simulated` in the database and UI.
- Duplicates and conflicting order references are resolved in code before any model call, so they cost nothing and cannot be "talked into" a new order.

**Checks.** 11 pipeline tests with a scripted fake model (`tests/conftest.py`): full batch statuses and order count vs `data/reference/expected.json`, idempotent reprocessing (no new model calls, no new orders), model-unavailable then retry, invalid output after a bounded retry, a model that picks a SKU contradicting the wording, reviewer correction persisted across a reopened database, approval rules.

**Correction.** The first replay run without recordings reported `MODEL_UNAVAILABLE`, which would mislead a reviewer into thinking the API was down. Added a separate `REPLAY_MISSING` code.

## Stage 4 — Review UI (2026-10-07)

**Goal.** Operations queue with status filter; request detail with the original beside the proposal, catalog evidence, failed checks, reviewer correction + revalidation + history; approval; exports.

**Design.** Server-rendered Jinja2 with HTMX only for the queue filter (partial `_queue_rows.html`, URL pushed so filters are linkable). htmx 2.0.4 is vendored in `static/` so the app runs offline. Dashboard shows status counts, exception reasons with example links, duplicates, model-call sources (live/replay/simulated), and a data-backed improvement suggestion. Optional enhancements included: model-vs-current comparison table, approved-order export (JSON + CSV), image attachment display.

**Checks.** 6 web tests (`tests/test_web.py`): filter returns only matching rows as a partial, detail shows request, totals and lookups, full correction → approve → export flow through HTTP forms, invalid correction stays visible as `needs_clarification`, attachment route blocks path traversal.

**Correction.** The first filter test asserted that "R1" must not appear in the duplicate-only rows, but the duplicate row correctly links to the request it repeats (R1). The test was wrong, not the app; changed it to count rows.
