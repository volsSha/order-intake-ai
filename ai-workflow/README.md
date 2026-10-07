# AI workflow used during this exercise

Only configuration that was actually used is copied here. Every record, with its status, is listed in [`manifest.json`](manifest.json).

## Tools and models

**Development time.** Claude Code 2.1.292 (CLI, WSL2) with Claude Opus 5.5 (`claude-opus-5-5`), effort `high`, permission mode `auto`.
- No subagents, plugins or MCP servers were used.
- The local toolchain was Python 3.12, uv 0.11.2, pytest and ruff.

**Inside the application.** `openai/gpt-6-luna` through OpenRouter (`https://openrouter.ai/api/v1`), with OpenAI direct as the fallback when no OpenRouter key is set.

| Setting | Value |
|---|---|
| Reasoning effort | `low` |
| `seed` | `7` |
| `max_completion_tokens` | `4000` |
| `tool_choice` | `required` (strict tool schemas) |
| Model calls per request | at most 6 |
| Repair retry | 1 |
| Timeout | 60 s |
| Temperature | provider default |

The client is the `openai` Python SDK 3.26.0. All of these settings are in `src/order_intake/config.py`, and the variable names are in [`.env.example`](.env.example).

## Configuration files

| What | Where | Scope |
|---|---|---|
| Project agent instructions | [`CLAUDE.md`](../CLAUDE.md) | project, normal location |
| Application system prompt | [`prompts/extract_order.md`](../prompts/extract_order.md) (v1 `eceda71`, v2 `bcb1c4c`) | project |
| Application tools | [`src/order_intake/schemas.py`](../src/order_intake/schemas.py) | project |
| Application model settings | [`src/order_intake/config.py`](../src/order_intake/config.py), [`.env.example`](../.env.example) | project |
| Data-generation skill (supplied) | [`starter/skills/generate-assignment-data/SKILL.md`](../starter/skills/generate-assignment-data/SKILL.md) | starter pack, followed as guidance |
| FastAPI skill (full folder: `SKILL.md`, `assets/`, `references/`, `scripts/`) | [`snapshots/skills/fastapi/`](snapshots/skills/fastapi/) | user, copied as is |
| User-level instructions (excerpt) | [`snapshots/user-CLAUDE.excerpt.md`](snapshots/user-CLAUDE.excerpt.md) | user, sanitized |
| User-level settings (excerpt) | [`snapshots/user-claude-settings.excerpt.json`](snapshots/user-claude-settings.excerpt.json) | user, sanitized |
| Hooks | [`snapshots/hooks/`](snapshots/hooks/) | user |
| Agent memory note | [`snapshots/agent-memory/`](snapshots/agent-memory/) | user, sanitized |

### Skills

- **`generate-assignment-data`** (from the starter pack) shaped the dataset:
  - the rules stay fixed;
  - expected results live in a separate file and are written by hand;
  - the arithmetic is checked by code that does not import the app (`scripts/verify_reference.py`).
- **`fastapi`** (user level) was invoked after the build to review `src/order_intake/web/app.py`. It found two defects, both fixed with a test:
  - `async def correct` took a `threading.Lock` and wrote to SQLite on the event loop. While `/process` held the lock during live model calls, the whole server would stall. The work now runs in `run_in_threadpool`.
  - Error messages were put into redirect URLs without encoding, so a message containing `&` or `#` would be cut off. They are now URL-encoded.

### Hooks

Both hooks are `PreToolUse` hooks, so they run before each matching tool call by the assistant. Neither is needed to run or review the application.
- **`strip-claude-attribution.py`** runs on Bash, Write and Edit. It denies a call that would add the Claude Code attribution footer or a co-author trailer naming Claude, and it never modifies anything. It fired once during this exercise: an early draft of this README quoted the trailer literally, the write was denied, and the text was reworded.
- **`rtk-rewrite.sh`** runs on Bash. It sends shell commands through the third-party `rtk` CLI to compress their output. Side effect seen here: `find` and `cat` output was altered, so `command cat` was used where the exact file contents mattered.

### Redactions and omissions

These were left out, and their values are not shown:
- plugin and marketplace settings (not used, and one source is a private Git URL);
- telemetry and auto-compact toggles;
- cosmetic UI settings;
- a SessionStart hook for a disabled plugin;
- one section of the user-level CLAUDE.md about an unrelated tool;
- home-directory paths, replaced with `~`.

API keys were never written to the repository. The `Read(**/.env*)` deny rules also kept the assistant from reading the local `.env`.

### Restore

Project files are already in their normal locations. For the user-level parts:
1. Merge the settings excerpt into `~/.claude/settings.json`.
2. Copy the hooks to `~/.claude/hooks/` and make them executable. The rtk hook also needs `rtk` and `jq`.
3. Copy `snapshots/skills/fastapi/` to `~/.claude/skills/fastapi/`; `python scripts/validate.py` inside it checks the structure.

## One workflow example

Full write-up: [`docs/LLM_USAGE.md`](../docs/LLM_USAGE.md).

- **Instruction.** *"Also add OpenAI as a replacement for OpenRouter when the OpenRouter key is not set."*
- **Configuration that affected it.**
  - `CLAUDE.md` requires `pytest` and `ruff` before finishing, and keeps secrets out of the repository.
  - The `.env` read-deny rule meant the assistant could only check that the file existed.
- **Check.** 8 new tests in `tests/test_llm.py`.
- **Correction.** One test exposed a real bug: displaying replay paths with `relative_to(ROOT)` crashed when the replay directory was outside the repository. Fixed in `c6f39aa`.

A second example comes from the application model's real output. The first live recording produced the clarification "Could you clarify which Moon adapter you mean?", which implies that the unknown product exists. The prompt was tightened, and code now adds a fixed frame with the order reference. The batch was then re-recorded; commits `9b76240` → `bcb1c4c` show the before and after.

## Reproduce or replay

```bash
uv sync
uv run order-intake check      # replays recorded real responses: no API key, no model calls
uv run order-intake serve      # review UI on http://127.0.0.1:8000
```

To call the model again:
1. `cp .env.example .env`
2. Set `OPENROUTER_API_KEY` or `OPENAI_API_KEY` in `.env`.
3. Run `uv run order-intake process --mode live --db var/live.db`.

New recordings go to `replay/`.

## Decisions and limitations

- I chose a plain SDK and a short tool loop over an agent framework. The step limit, the retry and the recording stay visible and testable.
- The development setup is close to default: Claude Code, a project `CLAUDE.md`, two existing user hooks and two skills. Nothing was created just to fill the manifest.
- Not exportable: the development chat history (summarised in [`docs/DEV_JOURNAL.md`](../docs/DEV_JOURNAL.md)), and the Claude Code system prompt.
- What I would change: a project-level hook that runs `ruff` and `pytest -x` after edits, instead of relying on the instruction in `CLAUDE.md`. I would also apply the FastAPI review during the UI stage, not after it.
