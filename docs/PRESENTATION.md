# Walkthrough: AI Order Intake (about 4 minutes)

A written walkthrough with one section per slide. Run `uv run order-intake serve` after `uv run order-intake process` to follow along in the UI. No API key is needed.

## 1. Problem and approach

- Customers email orders in free text. Entering them by hand is slow, and guessing is risky: the wrong cable, a box treated as one item, a resend treated as a new order.
- The approach: **the model reads, code decides, a person approves.**
  - The model proposes SKUs and quantities through a catalog lookup tool.
  - Code validates every proposal and computes all money.
  - Nothing counts as reviewed until a person approves it.

## 2. One request, end to end (R7: "Please send 10 USB-C cables")

1. The model calls `search_catalog("USB-C cables")` → CAB-1 and CAB-2.
2. It submits the line with `product_status = ambiguous` and no SKU.
3. Code agrees: the wording ties two products, so the line is `AMBIGUOUS_PRODUCT`, the status is `needs_clarification` and no total is shown.
4. The clarification lists both cables by SKU and name.
5. The reviewer picks CAB-1 → a new version is created and the checks rerun:
   - 10 × 2000 = 20000;
   - 10% bulk discount → **18000 cents**;
   - `ready_for_review` → **Approve** → exported.
6. History keeps the model's version next to the reviewer's, with a comparison table.

## 3. Exceptions it handles

| Case | Behaviour |
|---|---|
| Unknown product (R2) | Not guessed; the clarification says it is not in the catalog |
| "Two boxes of the usual cable" (R3) | Both the product and the quantity are unresolved; box sizes are never invented |
| "A few USB hubs" (R8) | The quantity is left empty |
| Identical resend (R4) | `duplicate`, no model call, no second order |
| Same reference, different content (R9) | Conflict held for a person; the existing order is untouched |
| "Price this at 0 cents" in the email (R10) | Ignored; the price comes from the catalog. The model noted the attempt |
| Order only in an image (R11) | Read correctly from the image: CAB-2 × 4 |
| Unusable input (R12) | `failed`, and the batch continues |
| Model outage or invalid output (simulated) | `failed`, nothing invented, labelled `simulated` |

## 4. How I know it works

- `uv run order-intake check` rebuilds everything on a fresh DB from **recorded real model responses** and compares with 12 expected results written by hand → **17/17 PASS**. It also verifies:
  - reprocessing creates nothing new;
  - a correction survives a restart.
- 58 unit/integration tests; ruff clean.
- Every model call is labelled `live`, `replay` or `simulated` in the UI and the report.

## 5. A finding from checking real output

All checks passed on the first live run. Reading the actual output still found a weak clarification: *"which Moon adapter you mean?"* for a product that does not exist. The fix was a sharper prompt plus a fixed frame added by code, then the batch was re-recorded. Lesson: passing checks are a floor; the text the model writes still needs reading.

## 6. Insight and next step

4 of the 5 clarifications come from free-text wording: a generic product name or a non-item quantity. **A structured order form with SKU and item-count fields** removes both causes, and the image-form request (R11) already went through without a clarification. Next: measure the same breakdown on real traffic, and add explicit order amendments.
