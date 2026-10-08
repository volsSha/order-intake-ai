# Submission notes

## What the brief requires

The brief's *Submission* section asks for a **Git repository** with the solution, dependencies, and small sample and replay files containing no sensitive data. It does not say whether the repository should be public or private, or how to deliver it.

| Brief item | Where |
|---|---|
| Git repository with solution, dependencies, samples, replay files | This repo: [`uv.lock`](../uv.lock), [`data/`](../data/README.md), [`replay/`](../replay/README.md) |
| Dataset and starter material: seeds and rules, additions, generation method, assumptions, five checked reference cases | `starter/` (unmodified, with checksums), `data/`, `data/GENERATION.md`, `docs/ASSUMPTIONS.md`, `data/reference/expected.json` (REF-1…REF-5 marked as the minimum demonstration) |
| README: setup, run, architecture, model configuration, data assumptions, time spent, limitations, replay without a key | [`README.md`](../README.md), with [`ARCHITECTURE.md`](ARCHITECTURE.md) and the folder indexes |
| Brief presentation | `docs/PRESENTATION.md` (written walkthrough) |
| LLM usage note | `docs/LLM_USAGE.md` |
| AI configuration | `ai-workflow/manifest.json`, `ai-workflow/README.md`, `ai-workflow/snapshots/`, `CLAUDE.md`, `prompts/` |
| Check results, expected vs observed | [`reports/minimum-demonstration.md`](../reports/minimum-demonstration.md), plus the LLM-judge evaluation in [`reports/judge-report.md`](../reports/judge-report.md) |
| Optional enhancements | Image input (R11), model-vs-reviewer comparison, approved-order export, LLM-as-judge with calibration and baseline, CI |

## Delivery

- Public GitHub repository: https://github.com/volsSha/order-intake-ai. The full commit history is kept, one commit per stage, with no squashing. The history shows the work and the corrections.
- If the hiring team prefers not to have a public copy of the assignment, the repository can be made private with reviewer access added, or sent as a `git bundle`, which keeps the history.

## Pre-submission checklist

- [x] No secrets in tracked files or history. `.env` and editor swap files are gitignored; replay files contain no keys or headers.
- [x] `uv run order-intake check` and `uv run order-intake judge` pass **with no API key**.
- [x] `uv run pytest --cov` (235 tests, coverage gate 90%) and `uv run ruff check .` pass.
- [x] CI is green on GitHub: a clean machine with no secrets runs lint, tests, `check` and `judge`.
- [x] Commits authored as the submitting candidate.
- [x] Fresh clone smoke test: `git clone … && uv sync && uv run order-intake check` passes with no key. CI repeats this on every push.
- [ ] Tag the submitted commit: `git tag submission-v1 && git push --tags`.
