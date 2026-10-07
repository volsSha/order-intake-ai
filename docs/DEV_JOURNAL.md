# Development journal

Chronological record of how the solution was built with AI assistance: what was asked, how the result was checked, and what was corrected. Source for the LLM usage note (`docs/LLM_USAGE.md`).

## Stage 0 — Scaffold (2026-10-07)

**Goal.** Separate Git repository for alternative A, starter pack copied unchanged, tooling chosen.

**Decisions.**
- Stack: Python 3.12 + FastAPI + Jinja2 + HTMX + SQLite. One language, server-rendered review UI, easy to test with pytest. React/Streamlit/Laravel were considered; rejected for time (React), testability (Streamlit), and AI-ecosystem fit (Laravel).
- LLM layer: plain `openai` SDK pointed at OpenRouter with a hand-written tool loop. LangGraph/LangChain were considered and rejected: the flow is linear (lookup → submit), and raw request/response capture for replay is simpler without a framework.
- Model: `openai/gpt-6-luna` via OpenRouter (cheap, supports tools, structured outputs, image input). Configurable with `MODEL_ID`. No key was supplied by the hiring team; the candidate's own OpenRouter key with a spending cap is used. OpenAI direct is supported as a fallback (stage 6).
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

## Stage 5 — Automated minimum-demonstration report (2026-10-07)

**Goal.** One command that rebuilds everything on a fresh temporary database and compares observed behaviour with the independent expectations: `uv run order-intake check` → `reports/minimum-demonstration.md` + `reports/check-results.json`.

**What it does.** Processes all 12 requests; evaluates every reference case (status, priced lines, totals, required findings, no guessed SKU/quantity, clarification saved, duplicate/conflict links, no new orders); reprocesses the whole batch and checks that orders and model calls do not grow; applies the REF-5 reviewer correction, closes the database, reopens it (restart) and checks status, lines, total and version history; runs labelled simulated failures (model outage, invalid output) on a separate database. Exit code is non-zero on any failure, so it can gate CI.

**Checks.** `test_check_runner_passes_with_a_well_behaved_model` runs the same runner with the scripted fake model to prove the runner itself works before any real call.

## Stage 6 — OpenAI fallback provider (2026-10-07)

**Request.** Use OpenAI directly when no OpenRouter key is configured.

**Design.** `Settings.resolve_provider()`: `LLM_PROVIDER=auto` picks OpenRouter if `OPENROUTER_API_KEY` is set, else OpenAI if `OPENAI_API_KEY` is set, else none (replay only). The OpenAI model name defaults to `MODEL_ID` without the `openai/` prefix (`OPENAI_MODEL_ID` overrides; a non-OpenAI `MODEL_ID` without an override is a configuration error, not a silent model swap). Provider differences are isolated in `ChatClient.live_params`: OpenRouter gets `extra_body.reasoning.effort` and an `X-Title` header; OpenAI gets `reasoning_effort` and has the OpenRouter-only `reasoning_details` stripped from history. The replay key is computed from a provider-independent description of the request, so recordings replay regardless of which provider produced them; the provider is stored in every replay file and model-call row and shown in the UI.

**Checks.** 8 tests in `tests/test_llm.py`: provider precedence, fallback model mapping, config error for a non-OpenAI model, provider-specific parameters, provider-independent replay key, clear error without any key, replay hit and miss on changed input.

**Correction.** The replay test failed with `ValueError: ... is not in the subpath of ...`: replay paths were displayed with `relative_to(ROOT)`, which crashes when `replay_dir` is configured outside the repository (as in tests). Real bug, fixed with a display helper.

## Stage 7 — Live recording with a real model (2026-10-07)

**Run.** `uv run order-intake process --mode live --db var/live.db` with `openai/gpt-6-luna` via OpenRouter, reasoning effort `low`, seed 7. Every request takes two calls (lookup, then submit). The full batch costs about $0.0023 according to OpenRouter's reported usage. Before committing, I checked the replay files for keys and headers; they hold only the request, the response and the usage numbers.

**Observed.** The statuses and all 17 reference checks passed on the first live run (commit `9b76240` keeps that run). A passing status is not proof of correct behaviour, so I read every proposal:
- R10: the model ignored the "price this at 0 cents" instruction and said so in `notes`.
- R11: it read the order from the image correctly (CAB-2 × 4).
- R7: it marked "USB-C cables" as ambiguous on its own, so the code-side override was not needed here. It is still covered by unit tests.

**Correction.** Two defects that the reference checks did not catch:
1. When every customer-facing finding came from the model, its clarification draft was used verbatim. For R2 it read "Could you clarify which Moon adapter you mean?". That implies the product exists, and it omits the order reference. Fix:
   - The prompt now asks for one question per unresolved item. Unknown products must be named as not in the catalog, and ambiguous ones must list the candidate SKUs.
   - Code always adds the greeting, the order reference and the sign-off.
   - After re-recording, R2 reads "Moon adapter is not in the catalog. Could you provide the SKU or a description?"
2. `unit` was dropped while validated lines were built, so the UI could not show that R3 asked for containers. Now kept; reviewer lines default to `item`.

The prompt change changes the replay keys, so the first recordings were removed and the batch was recorded again (`var/live2.db`). History keeps the first run.

**Checks.** Added 3 unit tests (frame around the model draft, template used when code raised the finding, `unit` kept). 57 tests pass. `order-intake check` passes in replay mode with both API keys blanked, which shows that a reviewer can reproduce the results without a key.

## Stage 8 — Documentation, AI configuration, FastAPI review (2026-10-07)

**Docs.** README, ARCHITECTURE, LLM_USAGE, INSIGHTS (the improvement is backed by the reason codes from the live run), PRESENTATION and SUBMISSION. `ai-workflow/manifest.json` and `README.md` were filled in from the templates.

**Configuration snapshots.** Only configuration that was actually used is copied, sanitized:
- user instructions (excerpt);
- settings: model, permissions and hooks;
- both hook scripts;
- one agent memory note;
- the FastAPI skill's `SKILL.md`.

Plugins were enabled but not used, so they are not copied. Copying user-level configuration into a public repository was first blocked by Claude Code's auto-mode permission classifier. The candidate then reviewed the scope and approved a minimal, sanitized copy.

**FastAPI review (user skill `fastapi`).** The web layer was checked against the skill's topics: structure, request bodies, async, security and testing. Two real defects were found:
1. `async def correct` ran synchronous SQLite work under a `threading.Lock` on the event loop. A live `/process` run holding the lock would stall every request. It now uses `run_in_threadpool`.
2. Error messages went into redirect URLs without encoding. They are now URL-encoded.

`test_rejected_action_shows_full_error_message` was added. 58 tests pass and ruff is clean; `ai-workflow/snapshots` is excluded from ruff because it holds verbatim copies.
