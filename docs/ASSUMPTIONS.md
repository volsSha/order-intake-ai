# Ambiguities and assumptions

The supplied rules (`starter/orders/domain.md`) are the source of truth. Where they are silent, the choice below is the most conservative one: keep the issue visible for a person instead of inventing a policy.

| ID | Question the rules leave open | Choice made | Why |
|---|---|---|---|
| A1 | Rule 4 covers *identical* reprocessing. What if a request reuses an existing order reference with **different** content (R9)? | No new order and no change to the existing draft. The request is marked `needs_clarification` with `CONFLICTING_ORDER_REF` and linked to the earlier request. | "Same order reference = same order", so it cannot be a new order; silently applying the change would be an invented amendment policy. |
| A2 | When are two requests "identical"? | Same order reference and same body after trimming whitespace and normalising line endings. Headers such as `Request-ID` are ignored. | R4 differs from R1 only by its request ID. |
| A3 | A request without an order reference (R12). | `failed` with `UNUSABLE_INPUT`; the model is not called. | Duplicate prevention depends on the reference; the starter template treats it as required. |
| A4 | Containers ("boxes", "packs", "cases"). | Always need clarification (`NON_ITEM_UNIT`). | Rule 1: do not infer how many items a box contains. |
| A5 | Vague amounts ("a few", "some", "enough"). | `AMBIGUOUS_QUANTITY`; quantity left empty. | Rule 3: ambiguous quantities need clarification. Number words ("two", "twelve") are explicit counts. |
| A6 | "The usual cable" — implies order history. | Treated as ambiguous product. | No order history exists in the pack. |
| A7 | Prices or instructions written in the request (R10). | Ignored; unit price always comes from the catalog. | Rule 2: use the catalog unit price. Request text is data, not instructions. |
| A8 | The same SKU on two lines (e.g. 5 + 5). | Flagged `DUPLICATE_SKU_LINES` for review, not merged. | Merging would change discount eligibility (rule 2 is per line); the rules do not say which is right. |
| A9 | Rounding when 10% is not a whole cent. | `discount = (subtotal * 10 + 50) // 100` in integer cents (half up). | Rule 2. Never triggered by the supplied catalog; covered by unit tests. |
| A10 | What counts as "reviewed by a person"? | Separate `approved` status, set only by the reviewer action. A model draft that passes validation is `ready_for_review`, not approved. | Brief: distinguish a structurally valid draft from an order reviewed by a person. |
| A11 | Reviewer edits after approval. | Any correction returns the order to validation; approval must be given again. | Approval should refer to the exact saved version. |
| A12 | "A dozen", "half a dozen", "two dozen". | Read as 12, 6 and 24 individual items. "Dozens" on its own stays vague (`AMBIGUOUS_QUANTITY`). | A dozen is a fixed count, not a container, so rule 1 does not apply. Added in stage 10 after the judge flagged J1. |
| A13 | Lengths written out ("two-metre", "2-meter", "2m"). | Read as the catalog's "2 m" when matching a description. | Same product, different spelling. Without this, "two-metre USB-C cables" tied CAB-1 and CAB-2 (J1). |
