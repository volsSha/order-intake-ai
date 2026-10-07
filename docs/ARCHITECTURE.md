# Architecture and design decisions

## Division of work between model and code

| Step | Who | Why |
|---|---|---|
| Parse the request file and reject unusable input | Code | Needs no model, and must work while the model is down |
| Detect a duplicate or a conflicting order reference | Code, before any model call | Rule 4 is exact; it costs nothing and is never wrong |
| Read the free text or image and find the products and quantities | **Model** | The one step that needs language understanding |
| Catalog lookup | Code, exposed as the `search_catalog` tool | The model sees only what the catalog returns |
| Decide whether a proposal can be accepted | Code (`validation.py`) | Checks that cannot be argued around (below) |
| Price, discount, total | Code (`pricing.py`), integer cents | The model never produces a number that ends up in a total |
| Clarification text | Model writes the questions, code adds the fixed frame | The model's wording is used only when the model itself raised every issue |
| Approval | A person | `ready_for_review` ≠ `approved` |

## Validation checks on a model proposal

A model line is accepted only if every check passes. Otherwise it is kept, flagged and left unpriced.

1. **The SKU exists** in the catalog (`SKU_NOT_IN_CATALOG`).
2. **The SKU came from a `search_catalog` result in the same conversation** (`SKU_NOT_FROM_LOOKUP`). This blocks SKUs the model made up or remembered.
3. **The wording matches exactly one product.** Code re-runs the description match against the catalog. If several products tie, the line is `AMBIGUOUS_PRODUCT` even when the model said `matched`. Test case: the model picks CAB-1 for "USB-C cables".
4. **The model's own uncertainty is respected**: `unknown`/`ambiguous` product status, `container`/`unclear` unit, vague quantity.
5. **The quantity appears in the request text**, as digits or a number word (`QUANTITY_NOT_IN_SOURCE`). This check is skipped for image-only requests, which have no text to compare against.
6. **The product wording appears in the request text.** If not, a warning is raised; this check does not block the line.

Reviewer corrections go through the same `validate_lines` with `author="reviewer"`. Checks 2–6 describe what the model read, so they do not apply to a reviewer. The catalog check and the positive-integer quantity check still apply.

## Tool loop (`extraction.py`)

- Two strict function tools (`additionalProperties: false`, all fields required), generated from Pydantic models.
- `tool_choice="required"`, so every turn is a tool call. The model must search before it submits.
- At most 6 model calls per request. One repair retry when `submit_order_draft` arguments fail schema validation; after that the request is `failed` with `INVALID_MODEL_OUTPUT`. Nothing is invented.
- No agent framework. LangGraph or similar was considered and rejected: the loop is about 60 lines, and a framework would hide the step limit and the recording.

## Record and replay (`llm.py`)

- The replay key is the SHA-256 of a provider-independent description of the call: model ID, messages (images reduced to their hash), tools, `tool_choice`, `max_completion_tokens`, `seed` and reasoning effort.
  - Any change to the prompt, the request or the parameters produces a new key. A stale recording can never be served for a different input.
  - A recording made through OpenRouter replays even when OpenAI is configured, and the other way round.
- One file per call: `replay/<request>/stepNN-<key>.json`. Each file holds the provider, model, latency, request, raw response and usage/cost. Keys and headers are never written.
- Every model call row has a `source`, shown as a badge in the UI and the report:
  - `live` = a new API call;
  - `replay` = a saved real response;
  - `simulated` = a labelled test failure with no API call.
- A missing recording in replay mode is `REPLAY_MISSING`, not `MODEL_UNAVAILABLE`.

## Storage (`storage.py`, SQLite)

- `orders.order_ref` is the primary key, which makes one order per reference a database constraint.
- `proposals` are versioned and append-only. The author is `model`, `system` or the reviewer's name. A correction adds a version; earlier versions are never overwritten.
- `llm_calls`, `tool_calls` and `audit_log` make each proposal traceable to the calls that produced it.
- Reprocessing is idempotent: a request that has already been processed is skipped, so neither the order count nor the model-call count grows. `order-intake check` tests this.

## Providers

`LLM_PROVIDER=auto` uses OpenRouter if its key is set, otherwise OpenAI directly, otherwise none (replay only). Provider differences sit in one function, `ChatClient.live_params`:
- OpenRouter gets `extra_body.reasoning`;
- OpenAI gets `reasoning_effort`.

A non-OpenAI `MODEL_ID` with no `OPENAI_MODEL_ID` is a configuration error, not a silent model swap.

## UI

Server-rendered Jinja2 with htmx 2.0.4, vendored so the app works offline. htmx is used only where it helps (the queue filter). Everything else is plain forms with redirects, so every state has a URL and a reload never loses work.

## Alternatives not taken

| Option | Why not |
|---|---|
| Let the model compute totals | Arithmetic and the discount rule are deterministic; a model error would be silent |
| Let the model return a final JSON in one call, without tools | The SKU could not be tied to a real catalog lookup, and the model would see the whole catalog instead of the relevant results |
| Embeddings or vector search for products | Overkill for a 3-item catalog. The ambiguity decision must stay in code anyway |
| Merge repeated SKUs | Would change discount eligibility; the rules do not decide this (assumption A8) |
