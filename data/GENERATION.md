# Dataset and generation method

All data is fictional. Business rules come only from `starter/orders/domain.md`.

## Files

| Path | Origin |
|---|---|
| `data/catalog.json` | Copied verbatim from `starter/orders/seed.json` (`catalog`). |
| `data/requests/R1.txt` – `R4.txt` | Seed requests from `starter/orders/seed.json`, wrapped as email-style files. Body text unchanged. |
| `data/requests/R5.txt` – `R12.txt` | Added for this exercise (handwritten with an AI assistant, then reviewed by hand). |
| `data/requests/attachments/R11-order-form.png` | Rendered by `scripts/render_attachments.py` (Pillow, deterministic, no random seed needed). |
| `data/reference/expected.json` | Expected behaviour, written by hand before running the app; arithmetic re-checked by `scripts/verify_reference.py`. |

## Request file format

Email-style text: header lines, a blank line, then the body. Mapping from the starter template (`request.template.json`):

| Template field | Request file |
|---|---|
| `id` | `Request-ID:` header |
| `order_ref` | `Order-Ref:` header |
| `text` | body after the blank line |
| (new, optional) | `Attachment:` header, path relative to `data/requests/` |

`From:` and `Subject:` are decoration for realism and are not used by any rule.

## Scenario coverage

| Request | Scenario | Why it was added |
|---|---|---|
| R1 | Normal order (seed) | Minimum demonstration 1 |
| R2 | Unknown product (seed) | Minimum demonstration 2 |
| R3 | Ambiguous product + box quantity (seed) | Minimum demonstration 3 |
| R4 | Duplicate of R1 (seed) | Minimum demonstration 4 |
| R5 | Product by description, 12 items → bulk discount | Discount rule, description match |
| R6 | Two lines, discount on one line only | "That line only" part of rule 2 |
| R7 | "10 USB-C cables" — two catalog matches | Reviewer-correction scenario (minimum demonstration 5); correct to CAB-1 → 18000, the worked example |
| R8 | "a few USB hubs" | Vague quantity in natural wording |
| R9 | Same order reference as R5, different content | Duplicate rule edge case (see assumption A1) |
| R10 | Text inside the email tries to set the price | Prices must come from the catalog, not from the model or the email |
| R11 | Order only in an image attachment | Optional enhancement: additional request format |
| R12 | No order reference, empty body | Unusable input must not stop the batch |

## Limits of this dataset

- 12 requests is a demonstration set, not evidence of accuracy on other data.
- Catalog prices are multiples of 1000 cents, so the 10% discount is always a whole number of cents and the half-up rounding rule is never exercised by these requests. Rounding is covered by unit tests with synthetic prices instead of adding catalog items (the brief says to keep the supplied catalog).
