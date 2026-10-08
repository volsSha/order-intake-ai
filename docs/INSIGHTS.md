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

## What the LLM judge added

Source: [`reports/judge-report.md`](../reports/judge-report.md) (DeepSeek judge, 3 samples per case, blind to expected answers).

- **It catches what deterministic checks cannot.** Of the 6 seeded defects, deterministic checks caught 5. The sixth was a clarification claiming the unknown "Moon adapter" is out of stock. It has a correct status, lines and finding codes, and only the judge failed it, quoting "The Moon adapter is out of stock this week". Judge-only detection was 6/6, with 0 false fails on the 9 clean cases and kappa 1.00. All 3 samples agreed on all 270 sample pairs, so the noise floor on this corpus is zero.
- **It found a real over-flagging gap on an unlabelled request.** J1 asks for "a dozen of the two-metre USB-C cables".
  - The model proposed the right answer: CAB-2 × 12.
  - Code then blocked it. The description matcher did not equate "two-metre" with "2 m", so CAB-1 and CAB-2 tied (`AMBIGUOUS_PRODUCT`). The number reader did not know "dozen" (`QUANTITY_NOT_IN_SOURCE`).
  - The judge failed ambiguity handling and clarification quality, all three samples agreeing, with the rationale "the two-metre cable is unambiguous … a dozen is a clear count".

  The reference set could not have shown this, because J1 has no expected answer. The conservative checks never guessed; the cost was an unnecessary clarification email.
- **Fixed in stage 10, and proven with the baseline.** The matcher now reads written-out lengths as the catalog's "2 m", and the number reader knows "a dozen", "half a dozen" and "N dozen" (assumptions A12, A13). Only J1's calls had to be re-recorded. The judge run against the committed baseline then showed:

  | Change | Case | Criterion | Before | After | Samples |
  |---|---|---|---|---|---|
  | improvement | J1 | ambiguity_handling | fail | pass | pass, pass, pass |
  | improvement | J1 | clarification_quality | fail | pass | pass, pass, pass |

  0 regressions in the other 88 compared case criteria, `check` still 17/17, calibration unchanged (6/6, 0/9). J1 is now `ready_for_review` at 12 × 3000 − 3600 = **32,400 cents**. The new baseline was written from that run.

The improvement suggestion above stands. A structured order form with SKU and item-count fields also removes this class of wording problem, because a form never says "two-metre".
