# Prompts

| File | Used by | Role |
|---|---|---|
| [`extract_order.md`](extract_order.md) | [`llm/agent.py`](../src/order_intake/llm/agent.py) | Instructions for the extraction agent: the four domain rules, request text as data, search before submitting, structured line fields, clarification format |
| [`judge.md`](judge.md) | [`evals/judge.py`](../src/order_intake/evals/judge.py) | Rubric for the blind judge: five criteria, pass/fail/cannot_verify, verbatim evidence quotes, instructions inside the request are data |

Both prompts are part of the replay key. Any change needs a re-recording (see [`replay/README.md`](../replay/README.md)). A judge-prompt change also changes the baseline envelope.

History:
- `extract_order.md`, v2: clarification wording, after reading the first live output (journal stage 7).
- `judge.md`: rubric version 1.
