# Data

All data is fictional. The rules come only from [`starter/orders/domain.md`](../starter/orders/domain.md).

| Path | Content |
|---|---|
| [`catalog.json`](catalog.json) | The 3-product catalog, copied verbatim from the starter seed |
| [`requests/`](requests/) | R1–R12: 4 seed requests plus 8 added scenarios, as email-style files. [`requests/attachments/`](requests/attachments/) holds R11's image order form |
| [`reference/expected.json`](reference/expected.json) | Expected behaviour for R1–R12, written by hand before running the app. REF-1 to REF-5 are the minimum demonstration. Checked by [`scripts/verify_reference.py`](../scripts/verify_reference.py) |
| [`judge-cases/`](judge-cases/) | J1–J3: unlabelled paraphrased requests, judged by the LLM judge but never added to the expected results |
| [`GENERATION.md`](GENERATION.md) | How each file was produced, the request file format, scenario coverage, limits of the dataset |

The assumptions made where the rules are silent are in [`docs/ASSUMPTIONS.md`](../docs/ASSUMPTIONS.md).
