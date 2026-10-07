# `order_intake` package

The model proposes, code decides, a person approves. The packages follow that split.

| Path | Responsibility |
|---|---|
| [`config.py`](config.py) | Settings from `.env` and environment. Provider resolution: OpenRouter, then OpenAI, then replay only. Judge model and family check |
| [`pipeline.py`](pipeline.py) | Batch processing, duplicate and conflict handling before any model call, reviewer corrections, approval |
| [`storage.py`](storage.py) | SQLite: requests, orders (one per order reference), versioned proposals, model calls, tool calls, audit log |
| [`cli.py`](cli.py) | `order-intake process / status / check / judge / serve` |
| **[`domain/`](domain/)** | **Deterministic business rules, no model involved** |
| [`domain/inbox.py`](domain/inbox.py) | Parses email-style request files; rejects unusable input |
| [`domain/catalog.py`](domain/catalog.py) | Catalog lookup by SKU or description words; ambiguity detection |
| [`domain/validation.py`](domain/validation.py) | Every check on a proposal: SKU from lookup, one matching product, item counts, containers, pricing, statuses, clarification text |
| [`domain/pricing.py`](domain/pricing.py) | Integer cents; 10% off lines with at least 10 items, rounded half up |
| **[`llm/`](llm/)** | **Model integration** |
| [`llm/agent.py`](llm/agent.py) | PydanticAI agent: `search_catalog` tool, `submit_order_draft` output, request cap and one repair, mapping of errors to codes |
| [`llm/replay.py`](llm/replay.py) | `ReplayModel`: records every real response and replays it without a key; labels calls `live`, `replay` or `simulated` |
| [`llm/providers.py`](llm/providers.py) | Builds the real OpenRouter or OpenAI model with seed, token limit, reasoning effort and timeout |
| [`llm/schemas.py`](llm/schemas.py) | The structured output the model submits |
| **[`evals/`](evals/)** | **Offline evaluation** |
| [`evals/check.py`](evals/check.py) | `order-intake check`: compares a fresh run with [`data/reference/expected.json`](../../data/reference/expected.json) |
| [`evals/judge.py`](evals/judge.py) | `order-intake judge`: blind DeepSeek rubric judge, quote verification, verdict aggregation |
| [`evals/trajectory.py`](evals/trajectory.py) | Deterministic agent-trajectory checks: cap, search before submit, searched SKUs, no repeats, one submit |
| [`evals/defects.py`](evals/defects.py) | Seeded defects used to calibrate the judge |
| [`evals/report.py`](evals/report.py) | Calibration metrics, Cohen's kappa, noise floor, baseline comparison, report writers |
| **[`web/`](web/)** | FastAPI + Jinja2 + HTMX review UI: dashboard, queue, detail with correction and approval, exports |

Design decisions and the reasons behind them: [`docs/ARCHITECTURE.md`](../../docs/ARCHITECTURE.md).
