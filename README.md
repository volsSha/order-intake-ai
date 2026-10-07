# AI Order Intake & Exception Handling

[![CI](https://github.com/volsSha/order-intake-ai/actions/workflows/ci.yml/badge.svg)](https://github.com/volsSha/order-intake-ai/actions/workflows/ci.yml)

Junior AI Engineer take-home, **alternative A**.

The app turns free-text and image customer orders into draft orders, flags what cannot be decided, and drafts a clarification. A person corrects and approves every draft.

**The model reads, code decides, a person approves.**
- A PydanticAI agent proposes SKUs and quantities through a catalog-lookup tool.
- Code validates every proposal and computes all prices and statuses.
- A separate LLM judge from another model family grades the results offline.

| Result | Value |
|---|---|
| Reference checks on recorded real responses | **17/17 PASS** ([report](reports/minimum-demonstration.md)) |
| Batch of 12 requests | 9 orders: 5 ready for review, 5 need clarification, 1 duplicate, 1 failed |
| LLM judge on seeded defects | **6/6 caught** by the judge alone; 0/9 false fails on clean cases; kappa 1.00 ([report](reports/judge-report.md)) |
| Tests | 232, no network; coverage 97% of deterministic code (gate 90%); CI on every push |

## Run it: no API key needed

Requires Python 3.12 and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/volsSha/order-intake-ai.git && cd order-intake-ai
uv sync
uv run order-intake check     # reference checks from recorded real responses
uv run order-intake judge     # LLM-judge evaluation from recorded judge calls
uv run order-intake process   # process data/requests/ into var/intake.db
uv run order-intake serve     # review UI at http://127.0.0.1:8000
```

Everything runs from the recordings in [`replay/`](replay/). Without a key nothing is sent to a model provider.

Things to try in the UI:
1. Open **R7** ("10 USB-C cables"). The model flags it as ambiguous. Choose `CAB-1`, save, check that the total is $180.00 (10% bulk discount), approve, then export.
2. Open **R10**, an email that tries to set the price to 0.
3. Open **R11**, an order sent only as an image.

## Live mode

```bash
cp .env.example .env    # set OPENROUTER_API_KEY (or OPENAI_API_KEY for the pipeline)
uv run order-intake process --mode live --db var/live.db
uv run order-intake judge --mode live --samples 3
```

Settings and their defaults are in [`.env.example`](.env.example). The models are:
- pipeline: `openai/gpt-6-luna` on OpenRouter, with OpenAI direct as the fallback;
- judge: `deepseek/deepseek-v4.1-flash` on OpenRouter.

Recording the full pipeline and the judge costs a few cents.

## Where to find what

| I want to… | Go to |
|---|---|
| Understand the design and why | [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) |
| See the code map | [`src/order_intake/README.md`](src/order_intake/README.md) |
| See the data, scenarios and expected results | [`data/README.md`](data/README.md), [`docs/ASSUMPTIONS.md`](docs/ASSUMPTIONS.md) |
| See the check results | [`reports/README.md`](reports/README.md) |
| Understand the recordings and how to re-record | [`replay/README.md`](replay/README.md) |
| Read the prompts | [`prompts/README.md`](prompts/README.md) |
| Run or read the tests | [`tests/README.md`](tests/README.md) |
| Read the insights and the improvement suggestion | [`docs/INSIGHTS.md`](docs/INSIGHTS.md) |
| See how AI tools were used | [`docs/LLM_USAGE.md`](docs/LLM_USAGE.md), [`ai-workflow/README.md`](ai-workflow/README.md) |
| Follow the work stage by stage | [`docs/DEV_JOURNAL.md`](docs/DEV_JOURNAL.md) |
| Get a 5-minute walkthrough | [`docs/PRESENTATION.md`](docs/PRESENTATION.md) |
| Check what the brief asked for and where it is | [`docs/SUBMISSION.md`](docs/SUBMISSION.md) |
| See every doc | [`docs/README.md`](docs/README.md) |

## Time spent

About **5 hours** of my own time, working with an AI coding assistant (Claude Code with compound-engineering skills). Details in [`docs/LLM_USAGE.md`](docs/LLM_USAGE.md).

## Known limitations

- The 12 requests show the intended behaviour; they do not measure accuracy on real email traffic.
- The rounding rule is never triggered by the supplied catalog. Property-based tests cover it with synthetic prices.
- The description matcher is conservative. The judge found that code over-flags "a dozen" and "two-metre" on unlabelled case J1 (see [`docs/INSIGHTS.md`](docs/INSIGHTS.md)). This is a known follow-up.
- Clarifications are drafted, never sent. An order cannot be amended; a changed resend with the same reference stays a conflict for a person.
- There is one reviewer and no authentication.
- The judge is an offline evaluation tool. Its verdicts are not shown in the review UI.
