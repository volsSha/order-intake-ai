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
