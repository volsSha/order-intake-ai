"""Deterministic checks on a proposed order. The model proposes; this module decides."""

import re

from .catalog import Catalog
from .pricing import price_line

READY = "ready_for_review"
NEEDS_CLARIFICATION = "needs_clarification"
DUPLICATE = "duplicate"
FAILED = "failed"
APPROVED = "approved"
STATUSES = (READY, NEEDS_CLARIFICATION, APPROVED, DUPLICATE, FAILED)

BLOCKING = "blocking"
WARNING = "warning"

FINDING_LABELS = {
    "NO_LINES": "No product lines found",
    "UNKNOWN_PRODUCT": "Unknown product",
    "AMBIGUOUS_PRODUCT": "Ambiguous product",
    "PRODUCT_MISMATCH": "SKU does not match the product description",
    "SKU_NOT_IN_CATALOG": "SKU not in catalog",
    "SKU_NOT_FROM_LOOKUP": "SKU not returned by catalog lookup",
    "AMBIGUOUS_QUANTITY": "Ambiguous quantity",
    "NON_ITEM_UNIT": "Quantity in boxes/packs, not items",
    "INVALID_QUANTITY": "Invalid quantity",
    "QUANTITY_NOT_IN_SOURCE": "Quantity not supported by request text",
    "PRODUCT_TEXT_NOT_IN_SOURCE": "Product wording not found in request",
    "DUPLICATE_SKU_LINES": "Same SKU on several lines",
    "CONFLICTING_ORDER_REF": "Order reference already used with different content",
    "DUPLICATE_REQUEST": "Duplicate of an earlier request",
    "UNUSABLE_INPUT": "Unusable input",
    "MODEL_UNAVAILABLE": "Model unavailable",
    "REPLAY_MISSING": "No recorded model response to replay",
    "PROCESSING_ERROR": "Unexpected processing error",
    "INVALID_MODEL_OUTPUT": "Invalid model output",
    "STEP_LIMIT": "Model did not finish within the step limit",
}

CUSTOMER_FACING = {"UNKNOWN_PRODUCT", "AMBIGUOUS_PRODUCT", "AMBIGUOUS_QUANTITY", "NON_ITEM_UNIT", "NO_LINES"}

NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
    "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
    "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20,
}


def finding(code: str, message: str, line: int | None = None, severity: str = BLOCKING, source: str = "code"):
    return {"code": code, "severity": severity, "line": line, "message": message, "source": source}


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower()).strip()


def numbers_in(text: str) -> set[int]:
    found = {int(n) for n in re.findall(r"\d+", text)}
    found |= {NUMBER_WORDS[w] for w in re.findall(r"[a-z]+", text.lower()) if w in NUMBER_WORDS}
    return found


def validate_lines(
    lines: list[dict],
    catalog: Catalog,
    *,
    author: str,
    looked_up_skus: set[str] | None = None,
    source_text: str | None = None,
) -> dict:
    """Check every line, price the valid ones, and derive the status.

    author="model": the SKU must come from a lookup in the same conversation and agree with the
    product wording; quantity must be stated in the request. author="reviewer": a person chose
    the SKU and quantity, so only catalog membership and quantity validity are checked.
    """
    findings: list[dict] = []
    priced: list[dict] = []
    looked_up = {s.upper() for s in (looked_up_skus or set())}
    text = _squash(source_text) if source_text else None

    if not lines:
        findings.append(finding("NO_LINES", "The request does not name any product."))

    for i, line in enumerate(lines):
        before = len(findings)
        sku = (line.get("sku") or "").strip() or None
        item = catalog.get(sku)
        product_text = line.get("product_text") or ""
        status = line.get("product_status", "matched")

        if author == "model":
            if text is not None and product_text and _squash(product_text) not in text:
                findings.append(finding("PRODUCT_TEXT_NOT_IN_SOURCE",
                                        f"'{product_text}' does not appear in the request.", i, WARNING))
            if status == "unknown":
                findings.append(finding("UNKNOWN_PRODUCT", f"No catalog product matches '{product_text}'.",
                                        i, source="model"))
            elif status == "ambiguous":
                cands = ", ".join(line.get("candidate_skus") or []) or "several products"
                findings.append(finding("AMBIGUOUS_PRODUCT", f"'{product_text}' could be {cands}.", i,
                                        source="model"))
            elif not sku:
                findings.append(finding("UNKNOWN_PRODUCT", "Model marked the line matched but gave no SKU.", i))
            elif not item:
                findings.append(finding("SKU_NOT_IN_CATALOG", f"{sku} is not a catalog SKU.", i))
            else:
                if item.sku.upper() not in looked_up:
                    findings.append(finding("SKU_NOT_FROM_LOOKUP",
                                            f"{item.sku} was not returned by search_catalog for this request.", i))
                candidates = catalog.description_candidates(product_text)
                if len(candidates) > 1 and item.sku in candidates:
                    findings.append(finding("AMBIGUOUS_PRODUCT",
                                            f"'{product_text}' matches {', '.join(candidates)} equally; "
                                            f"the model chose {item.sku}.", i))
                elif candidates and item.sku not in candidates:
                    findings.append(finding("PRODUCT_MISMATCH",
                                            f"'{product_text}' best matches {', '.join(candidates)}, "
                                            f"not {item.sku}.", i))
        elif not item:
            findings.append(finding("SKU_NOT_IN_CATALOG", f"{sku or '(empty)'} is not a catalog SKU.", i))

        qty = line.get("quantity")
        qty_text = line.get("quantity_text") or ""
        if author == "model" and line.get("unit") == "container":
            findings.append(finding("NON_ITEM_UNIT",
                                    f"'{qty_text}' is not a count of individual items; box sizes are unknown.", i,
                                    source="model"))
        elif author == "model" and (line.get("quantity_status") != "explicit" or line.get("unit") == "unclear"):
            findings.append(finding("AMBIGUOUS_QUANTITY", f"'{qty_text or 'no amount'}' is not an exact item count.",
                                    i, source="model"))
        elif not isinstance(qty, int) or isinstance(qty, bool) or qty <= 0:
            findings.append(finding("INVALID_QUANTITY", f"Quantity must be a positive whole number, got {qty!r}.", i))
        elif author == "model" and text is not None and (
            _squash(qty_text) not in text or qty not in numbers_in(qty_text)
        ):
            findings.append(finding("QUANTITY_NOT_IN_SOURCE",
                                    f"Quantity {qty} is not stated as '{qty_text}' in the request.", i))

        line_codes = {f["code"] for f in findings[before:] if f["severity"] == BLOCKING}
        blocking_here = bool(line_codes)
        if line_codes & {"AMBIGUOUS_QUANTITY", "NON_ITEM_UNIT", "INVALID_QUANTITY"}:
            qty = None
        priced_line = {
            "index": i,
            "product_text": product_text,
            "sku": item.sku if item else sku,
            "name": item.name if item else None,
            "quantity": qty if isinstance(qty, int) and not isinstance(qty, bool) else None,
            "quantity_text": qty_text,
            "valid": not blocking_here,
        }
        if not blocking_here and item and isinstance(qty, int) and qty > 0:
            priced_line.update(price_line(item.unit_cents, qty))
        priced.append(priced_line)

    skus = [p["sku"] for p in priced if p["sku"]]
    for sku in {s for s in skus if skus.count(s) > 1}:
        findings.append(finding("DUPLICATE_SKU_LINES",
                                f"{sku} appears on more than one line; merging would change the discount.", None))

    blocking = [f for f in findings if f["severity"] == BLOCKING]
    total = sum(p["total_cents"] for p in priced) if lines and not blocking else None
    return {
        "lines": priced,
        "findings": findings,
        "order_total_cents": total,
        "status": NEEDS_CLARIFICATION if blocking else READY,
    }


def clarification_message(order_ref: str, findings: list[dict], lines: list[dict], catalog: Catalog,
                          model_draft: str | None = None) -> str | None:
    customer = [f for f in findings if f["code"] in CUSTOMER_FACING and f["severity"] == BLOCKING]
    if not customer:
        return None
    if model_draft and model_draft.strip() and all(f["source"] == "model" for f in customer):
        return model_draft.strip()
    questions = []
    for f in customer:
        line = lines[f["line"]] if f["line"] is not None and f["line"] < len(lines) else {}
        phrase = line.get("product_text") or "the product"
        if f["code"] == "UNKNOWN_PRODUCT":
            questions.append(f"We could not find \"{phrase}\" in our catalog. Could you send the SKU or describe it?")
        elif f["code"] == "AMBIGUOUS_PRODUCT":
            names = "; ".join(f"{i.sku} ({i.name})" for i in catalog.items
                              if i.sku in (catalog.description_candidates(phrase) or []))
            questions.append(f"\"{phrase}\" matches more than one product{': ' + names if names else ''}. "
                             "Which one do you need?")
        elif f["code"] in {"AMBIGUOUS_QUANTITY", "NON_ITEM_UNIT"}:
            questions.append(f"How many individual items of \"{phrase}\" do you need?")
        elif f["code"] == "NO_LINES":
            questions.append("Which products and how many individual items would you like?")
    body = "\n".join(f"- {q}" for q in dict.fromkeys(questions))
    return f"Hello,\n\nThank you for order {order_ref}. Before we prepare it, please confirm:\n{body}\n\nKind regards,\nOrder desk"
