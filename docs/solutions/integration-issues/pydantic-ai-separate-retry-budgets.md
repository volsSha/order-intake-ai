---
title: PydanticAI retry budgets are per kind, so "one repair" needs a hook
date: 2026-10-08
category: integration-issues
module: llm/agent
problem_type: integration_issue
component: service_layer
symptoms:
  - "An unknown tool name looped until UsageLimitExceeded instead of failing after one repair"
  - "A text-only reply followed by two invalid submits took 3 model calls, not 2"
root_cause: wrong_api
resolution_type: code_fix
severity: medium
framework_version: pydantic-ai-slim 2.54.0
tags: [pydantic-ai, retries, tool-output, agent-limits, record-replay]
---

# PydanticAI retry budgets are per kind, so "one repair" needs a hook

## Problem
The extraction agent must allow exactly one repair after any invalid model turn, then fail with `INVALID_MODEL_OUTPUT`. PydanticAI 2.54 has no single shared repair budget. It counts retries separately for each kind of failure, so a model could make several different mistakes before the agent stopped.

## Symptoms
- An unknown tool name looped until the request cap (`UsageLimitExceeded`, mapped to `STEP_LIMIT`) after 6 calls, instead of failing after one repair.
- A text-only reply, followed by invalid `submit_order_draft` arguments, took 3 model calls before `INVALID_MODEL_OUTPUT`.

## What Didn't Work
- **`ToolOutput(max_retries=1)` together with `retries={"output": 1}`.** This limits invalid output-tool arguments only. Text replies draw on the global output budget, and unknown tool names draw on the tools budget.
- **Lowering `retries["tools"]`.** That would also limit bad `search_catalog` arguments. The previous hand-written loop deliberately never charged those against the repair budget.

## Solution
Keep the per-kind budgets as they are, and add one `before_model_request` hook. Before each new model request, the hook counts the retry prompts that were not addressed to the search tool. ([`src/order_intake/llm/agent.py:57-69`](../../../src/order_intake/llm/agent.py))

```python
def _repair_budget() -> Hooks:
    hooks = Hooks()

    @hooks.on.before_model_request
    async def one_repair(ctx, request_context):
        repairs = sum(isinstance(p, RetryPromptPart) and p.tool_name != SEARCH
                      for m in request_context.messages if isinstance(m, ModelRequest) for p in m.parts)
        if repairs > 1:
            raise UnexpectedModelBehavior("Exceeded maximum output retries (1)")
        return request_context

    return hooks
```

It is passed as `capabilities=[_repair_budget()]` next to `retries={"tools": settings.max_steps, "output": 1}` and `end_strategy="exhaustive"` (`agent.py:75-81`). `extract()` already maps `UnexpectedModelBehavior` to `INVALID_MODEL_OUTPUT`, so no new error path was needed.

## Why This Works
PydanticAI turns every recoverable model mistake into a `RetryPromptPart` in the next request, whatever its kind. Counting those parts in the message history gives one budget across all kinds, while the framework keeps its own checks.

Retry prompts addressed to `search_catalog` are excluded, so a bad search query still costs only a step against the request cap, as before. Raising `UnexpectedModelBehavior` reuses the framework's own failure type, so callers see the same exception as for exhausted output retries.

## Prevention
- Before relying on retry limits, probe them with a `FunctionModel` that scripts each failure kind: text-only reply, unknown tool, invalid output arguments and invalid tool arguments. Assert the exact number of model calls for each.
- `tests/llm/test_agent.py` pins these limits:
  - `test_text_or_unknown_tool_then_invalid_submit_is_invalid_model_output` covers text-only and unknown-tool replies;
  - `test_invalid_submit_twice_is_invalid_model_output_with_two_calls` covers invalid output arguments.

  Removing the hook makes two of them fail.
- Pin `end_strategy="exhaustive"` as well. The default strategies can skip a `search_catalog` call sent in the same response as the submit, and that would fail the "SKU must come from a lookup" check. `test_search_in_the_same_response_as_submit_still_counts` fails with `end_strategy="early"`.

## Related Issues
- Journal stage 9, U3: [`docs/DEV_JOURNAL.md`](../../DEV_JOURNAL.md)
- Agent design and error-code table: [`docs/ARCHITECTURE.md`](../../ARCHITECTURE.md)
