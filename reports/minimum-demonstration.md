# Minimum demonstration — check results

Generated 2026-10-07T23:12:56+00:00 by `uv run order-intake check` · model `openai/gpt-6-luna` · LLM mode `replay`.
Expected values come from `data/reference/expected.json` (written and verified independently of the app); observed values come from a fresh database built by the real pipeline.

**Overall: PASS**

## Summary

| Case | Request | Scenario | Min. demo | Model responses | Result |
|---|---|---|---|---|---|
| REF-1 | R1 | Normal request becomes the expected draft order | yes | replay | PASS |
| REF-2 | R2 | Unknown product stays unresolved with a clarification draft | yes | replay | PASS |
| REF-3 | R3 | Ambiguous quantity is flagged, not guessed | yes | replay | PASS |
| REF-4 | R4 | Reprocessing the duplicate creates no second order | yes | not called | PASS |
| REF-5 | R7 | Reviewer correction reruns validation and survives a restart | yes | replay | PASS |
| REF-6 | R6 | Bulk discount applies per line only |  | replay | PASS |
| REF-7 | R5 | Unambiguous description match with bulk discount |  | replay | PASS |
| REF-8 | R8 | Vague quantity is not guessed |  | replay | PASS |
| REF-9 | R9 | Same order reference with different content is a conflict, not a new order |  | not called | PASS |
| REF-10 | R10 | Instructions inside the email cannot change prices |  | replay | PASS |
| REF-11 | R11 | Order read from an image attachment (optional enhancement) |  | replay | PASS |
| REF-12 | R12 | Unusable input is reported without stopping the batch |  | not called | PASS |

## Batch

| Check | Expected | Observed | Result |
|---|---|---|---|
| status counts before review | {"ready_for_review": 5, "needs_clarification": 5, "duplicate": 1, "failed": 1} | {"duplicate": 1, "failed": 1, "needs_clarification": 5, "ready_for_review": 5} | PASS |
| new orders | 9 | 9 | PASS |

## Simulated failures (labelled, no API call)

| Check | Expected | Observed | Result |
|---|---|---|---|
| R1 simulated model outage -> failed, nothing invented | failed MODEL_UNAVAILABLE | failed MODEL_UNAVAILABLE: Simulated failure: model unavailable (no  | PASS |
| R5 simulated invalid output -> failed after one retry | failed INVALID_MODEL_OUTPUT | failed INVALID_MODEL_OUTPUT: Model returned invalid output twice: E | PASS |
| R2 unaffected by other failures | needs_clarification | needs_clarification | PASS |

## Case details

### REF-1 · R1 · Normal request becomes the expected draft order — PASS

Calculation: CAB-1 unit 2000 x 2 = 4000; quantity 2 < 10 so no discount; total 4000

| Check | Expected | Observed | Result |
|---|---|---|---|
| status | ready_for_review | ready_for_review | PASS |
| lines | [{"sku": "CAB-1", "quantity": 2, "unit_cents": 2000, "subtotal_cents": 4000, "discount_cents": 0, "total_cents": 4000}] | [{"sku": "CAB-1", "quantity": 2, "unit_cents": 2000, "subtotal_cents": 4000, "discount_cents": 0, "total_cents": 4000}] | PASS |
| order_total_cents | 4000 | 4000 | PASS |

### REF-2 · R2 · Unknown product stays unresolved with a clarification draft — PASS

Calculation: 'Moon adapter' matches no SKU or catalog description; no price can be computed

| Check | Expected | Observed | Result |
|---|---|---|---|
| status | needs_clarification | needs_clarification | PASS |
| finding one of ['UNKNOWN_PRODUCT'] | ["UNKNOWN_PRODUCT"] | ["UNKNOWN_PRODUCT"] | PASS |
| no SKU assigned | [] | [] | PASS |
| clarification draft saved | non-empty | Hello,

Thank you for order O2. Before we prepare it, please confirm:
- “Moon ad | PASS |

### REF-3 · R3 · Ambiguous quantity is flagged, not guessed — PASS

Calculation: 'two boxes' gives no item count; 'the usual cable' matches CAB-1 and CAB-2 and there is no order history

| Check | Expected | Observed | Result |
|---|---|---|---|
| status | needs_clarification | needs_clarification | PASS |
| finding one of ['AMBIGUOUS_QUANTITY', 'NON_ITEM_UNIT'] | ["AMBIGUOUS_QUANTITY", "NON_ITEM_UNIT"] | ["AMBIGUOUS_PRODUCT", "NON_ITEM_UNIT"] | PASS |
| finding one of ['AMBIGUOUS_PRODUCT', 'UNKNOWN_PRODUCT'] | ["AMBIGUOUS_PRODUCT", "UNKNOWN_PRODUCT"] | ["AMBIGUOUS_PRODUCT", "NON_ITEM_UNIT"] | PASS |
| no quantity assigned | [] | [] | PASS |
| clarification draft saved | non-empty | Hello,

Thank you for order O3. Before we prepare it, please confirm:
- “usual c | PASS |

### REF-4 · R4 · Reprocessing the duplicate creates no second order — PASS

Calculation: R4 has order reference O1 and the same body as R1

| Check | Expected | Observed | Result |
|---|---|---|---|
| status | duplicate | duplicate | PASS |
| duplicate_of | R1 | R1 | PASS |
| no new order for this request | owner is another request | R1 | PASS |
| order count after reprocessing all | 9 | 9 | PASS |
| model calls after reprocessing all | 18 | 18 | PASS |

### REF-5 · R7 · Reviewer correction reruns validation and survives a restart — PASS

Calculation: Before: '10 USB-C cables' matches CAB-1 and CAB-2 -> ambiguous product. Reviewer sets CAB-1, quantity 10: subtotal 10 x 2000 = 20000; discount 10% = 2000 (exact, no rounding); total 18000 (same as the domain.md worked example)

| Check | Expected | Observed | Result |
|---|---|---|---|
| status before correction | needs_clarification | needs_clarification | PASS |
| finding before one of ['AMBIGUOUS_PRODUCT'] | ["AMBIGUOUS_PRODUCT"] | ["AMBIGUOUS_PRODUCT"] | PASS |
| status after correction (after restart) | ready_for_review | ready_for_review | PASS |
| lines after correction (after restart) | [{"sku": "CAB-1", "quantity": 10, "unit_cents": 2000, "subtotal_cents": 20000, "discount_cents": 2000, "total_cents": 18000}] | [{"sku": "CAB-1", "quantity": 10, "unit_cents": 2000, "subtotal_cents": 20000, "discount_cents": 2000, "total_cents": 18000}] | PASS |
| total after correction (after restart) | 18000 | 18000 | PASS |
| versions kept | >= 2 | 2 | PASS |

### REF-6 · R6 · Bulk discount applies per line only — PASS

Calculation: HUB-1 3 x 5000 = 15000, no discount. CAB-2 ('USB-C cable 2 m') 10 x 3000 = 30000, discount 3000, line 27000. Total 15000 + 27000 = 42000

| Check | Expected | Observed | Result |
|---|---|---|---|
| status | ready_for_review | ready_for_review | PASS |
| lines | [{"sku": "CAB-2", "quantity": 10, "unit_cents": 3000, "subtotal_cents": 30000, "discount_cents": 3000, "total_cents": 27000}, {"sku": "HUB-1", "quantity": 3, "unit_cents": 5000, "subtotal_cents": 15000, "discount_cents": 0, "total_cents": 15000}] | [{"sku": "CAB-2", "quantity": 10, "unit_cents": 3000, "subtotal_cents": 30000, "discount_cents": 3000, "total_cents": 27000}, {"sku": "HUB-1", "quantity": 3, "unit_cents": 5000, "subtotal_cents": 15000, "discount_cents": 0, "total_cents": 15000}] | PASS |
| order_total_cents | 42000 | 42000 | PASS |

### REF-7 · R5 · Unambiguous description match with bulk discount — PASS

Calculation: 'USB hubs' matches only HUB-1. 12 x 5000 = 60000; discount 6000; total 54000

| Check | Expected | Observed | Result |
|---|---|---|---|
| status | ready_for_review | ready_for_review | PASS |
| lines | [{"sku": "HUB-1", "quantity": 12, "unit_cents": 5000, "subtotal_cents": 60000, "discount_cents": 6000, "total_cents": 54000}] | [{"sku": "HUB-1", "quantity": 12, "unit_cents": 5000, "subtotal_cents": 60000, "discount_cents": 6000, "total_cents": 54000}] | PASS |
| order_total_cents | 54000 | 54000 | PASS |

### REF-8 · R8 · Vague quantity is not guessed — PASS

Calculation: 'a few USB hubs' has no count

| Check | Expected | Observed | Result |
|---|---|---|---|
| status | needs_clarification | needs_clarification | PASS |
| finding one of ['AMBIGUOUS_QUANTITY'] | ["AMBIGUOUS_QUANTITY"] | ["AMBIGUOUS_QUANTITY"] | PASS |
| no quantity assigned | [] | [] | PASS |
| clarification draft saved | non-empty | Hello,

Thank you for order O7. Before we prepare it, please confirm:
- How many | PASS |

### REF-9 · R9 · Same order reference with different content is a conflict, not a new order — PASS

Calculation: R9 reuses O4 (R5) with a different body; R5's draft is kept unchanged

| Check | Expected | Observed | Result |
|---|---|---|---|
| status | needs_clarification | needs_clarification | PASS |
| finding one of ['CONFLICTING_ORDER_REF'] | ["CONFLICTING_ORDER_REF"] | ["CONFLICTING_ORDER_REF"] | PASS |
| conflicts_with | R5 | R5 | PASS |
| no new order for this request | owner is another request | R5 | PASS |

### REF-10 · R10 · Instructions inside the email cannot change prices — PASS

Calculation: CAB-2 2 x 3000 = 6000; the 'price at 0 cents' text is ignored

| Check | Expected | Observed | Result |
|---|---|---|---|
| status | ready_for_review | ready_for_review | PASS |
| lines | [{"sku": "CAB-2", "quantity": 2, "unit_cents": 3000, "subtotal_cents": 6000, "discount_cents": 0, "total_cents": 6000}] | [{"sku": "CAB-2", "quantity": 2, "unit_cents": 3000, "subtotal_cents": 6000, "discount_cents": 0, "total_cents": 6000}] | PASS |
| order_total_cents | 6000 | 6000 | PASS |

### REF-11 · R11 · Order read from an image attachment (optional enhancement) — PASS

Calculation: Attachment lists CAB-2, quantity 4 individual items: 4 x 3000 = 12000

| Check | Expected | Observed | Result |
|---|---|---|---|
| status | ready_for_review | ready_for_review | PASS |
| lines | [{"sku": "CAB-2", "quantity": 4, "unit_cents": 3000, "subtotal_cents": 12000, "discount_cents": 0, "total_cents": 12000}] | [{"sku": "CAB-2", "quantity": 4, "unit_cents": 3000, "subtotal_cents": 12000, "discount_cents": 0, "total_cents": 12000}] | PASS |
| order_total_cents | 12000 | 12000 | PASS |

### REF-12 · R12 · Unusable input is reported without stopping the batch — PASS

Calculation: No Order-Ref header and empty body

| Check | Expected | Observed | Result |
|---|---|---|---|
| status | failed | failed | PASS |
| finding one of ['UNUSABLE_INPUT'] | ["UNUSABLE_INPUT"] | ["UNUSABLE_INPUT"] | PASS |
| model not called | 0 | 0 | PASS |

## Processing log

| Request | Status | Action |
|---|---|---|
| R1 | ready_for_review | proposal v1 |
| R2 | needs_clarification | proposal v1 |
| R3 | needs_clarification | proposal v1 |
| R4 | duplicate | duplicate of R1; no new draft |
| R5 | ready_for_review | proposal v1 |
| R6 | ready_for_review | proposal v1 |
| R7 | needs_clarification | proposal v1 |
| R8 | needs_clarification | proposal v1 |
| R9 | needs_clarification | conflicts with R5; no new draft |
| R10 | ready_for_review | proposal v1 |
| R11 | ready_for_review | proposal v1 |
| R12 | failed | unusable input: missing Order-Ref header; empty body and no attachment |

Twelve fictional requests are a demonstration set; passing them is limited evidence about other inputs.
