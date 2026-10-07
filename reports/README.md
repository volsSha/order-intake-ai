# Generated reports

All files here are generated. Re-create them without a key:

```bash
uv run order-intake check   # minimum-demonstration.md, check-results.json
uv run order-intake judge   # judge-report.md, judge-results.json (compares with judge-baseline.json)
```

| File | Content |
|---|---|
| [`minimum-demonstration.md`](minimum-demonstration.md) | The 12 reference cases, batch counts and simulated failures: expected against observed, 17/17 PASS |
| [`check-results.json`](check-results.json) | The same, machine-readable |
| [`judge-report.md`](judge-report.md) | The LLM-judge evaluation: calibration on seeded defects, the noise floor between samples, per-case verdicts with evidence quotes, baseline comparison |
| [`judge-results.json`](judge-results.json) | The same, machine-readable |
| [`judge-baseline.json`](judge-baseline.json) | Majority verdicts per case and criterion, plus the evaluation envelope (judge model, rubric version, corpus fingerprint). Written only with `--update-baseline` |
