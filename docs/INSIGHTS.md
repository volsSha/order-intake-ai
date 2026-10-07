# Insights: what causes exceptions, and one improvement

Data: the recorded live run (`openai/gpt-6-luna`, 12 requests). The same numbers appear on the UI dashboard.

## Where the exceptions came from

| Request | Status | Reason codes | Root cause |
|---|---|---|---|
| R2 | needs_clarification | `UNKNOWN_PRODUCT` | Product not in the catalog |
| R3 | needs_clarification | `AMBIGUOUS_PRODUCT`, `NON_ITEM_UNIT` | Generic wording ("the usual cable") plus a container ("two boxes") |
| R7 | needs_clarification | `AMBIGUOUS_PRODUCT` | Generic wording ("USB-C cables") matches 2 SKUs |
| R8 | needs_clarification | `AMBIGUOUS_QUANTITY` | Vague amount ("a few") |
| R9 | needs_clarification | `CONFLICTING_ORDER_REF` | Same order reference resent with different content |
| R12 | failed | `UNUSABLE_INPUT` | No order reference, empty body |
| R4 | duplicate | – | Identical resend, handled without a person |

**4 of the 5 clarifications (R2, R3, R7, R8) come from free-text wording**: the product is not named precisely, or the amount is not an item count. The model was not at fault in any of them: it flagged each one itself, and code-side checks agreed.

The requests that went straight to `ready_for_review` named the product in a way that matches exactly one item, and gave an item count:
- by SKU: R1, R10;
- by a unique description: R5 "USB hubs", and R6 "USB-C cable 2 m" next to "HUB-1";
- through a form: R11 is the image order form with SKU and quantity fields.

## Suggested improvement

**Give customers a structured order form, or a reply template, with a SKU field and a "number of items" field. Link it from every clarification.**

- It removes both causes seen here at the source: ambiguous products (R3, R7) and non-item or vague quantities (R3, R8).
- Unknown products (R2) become visible to the customer before they submit, because the form shows the catalog.
- R11 shows the pipeline already handles this format. A form arriving as an image was processed with no clarification.
- The clarification drafts already name the candidate SKUs (R3, R7), so customers can see the identifiers they should use.

The second, smaller change: **allow an explicit amendment** (for example "Amends: O4") so customers do not resend with the same reference (R9). Today such a resend is correctly held as a conflict, but it always needs a person.

## Caveats

- 12 hand-made requests show the categories; they are not a measured distribution. Before investing, I would take the same breakdown over a real month of requests. The dashboard computes it automatically from the reason codes.
- With a 3-item catalog, ambiguity is concentrated in one product family (the two USB-C cables). A larger catalog would likely make wording-based ambiguity more frequent, not less.
