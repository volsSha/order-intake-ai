# Recorded model responses

Every real model call is stored here, so the whole application, `check` and `judge` run without an API key. Nothing here contains a key or a request header. [`tests/test_repo_hygiene.py`](../tests/test_repo_hygiene.py) checks that on every run.

| Folder | Producer | Model |
|---|---|---|
| [`extract/<request>/`](extract/) | The extraction pipeline, one folder per request (`R*` from [`data/requests/`](../data/requests/), `J*` from [`data/judge-cases/`](../data/judge-cases/)) | `openai/gpt-6-luna` via OpenRouter |
| [`judge/<case>/`](judge/) | The LLM judge, one folder per case, three samples per case | `deepseek/deepseek-v4.1-flash` via OpenRouter |

## File format

`stepNN-<key16>.json`, one file per model request:

| Key | Content |
|---|---|
| `format_version` | `2` |
| `replay_key` | sha256 of the canonical request: messages without timestamps or provider ids, tool-call ids renumbered, images replaced by their hash, tool definitions, and the logical model id plus seed, token limit and reasoning effort from settings |
| `request` | That canonical request, readable |
| `response` | The serialized PydanticAI `ModelResponse`, including usage and cost |
| `provider`, `provider_model`, `latency_ms`, `recorded_at` | Where and when it was recorded |

The key does not depend on the provider, so a response recorded through OpenRouter replays when only OpenAI is configured. Changing a prompt, a request or a model setting changes the key: replay then stops with `REPLAY_MISSING`, never with a stale answer. A recording that exists but cannot be parsed stops with `REPLAY_CORRUPT`.

## Re-recording

```bash
uv run order-intake process --mode live --db var/live.db   # pipeline (needs OPENROUTER_API_KEY or OPENAI_API_KEY)
uv run order-intake judge --mode live --samples 3 --update-baseline   # judge (needs OPENROUTER_API_KEY)
uv run order-intake judge --mode auto --pipeline-mode auto             # record only the calls that are missing
```

`auto` replays every call that is already recorded and calls the model only on a miss. Stage 10 used it to re-record J1 after a validation fix, leaving every other recording untouched.

Delete the old files of a changed request first. The orphan test fails on any recording that a full replay run does not read.
