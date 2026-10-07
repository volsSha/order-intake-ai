"""Pricing rules from domain.md rule 2. Integer USD cents only."""

BULK_MIN_QUANTITY = 10
BULK_DISCOUNT_PERCENT = 10


def percent_half_up(amount_cents: int, percent: int) -> int:
    # (a * p + 50) // 100 rounds a*p/100 to the nearest cent, halves up, for non-negative amounts
    return (amount_cents * percent + 50) // 100


def price_line(unit_cents: int, quantity: int) -> dict[str, int]:
    if unit_cents < 0 or quantity <= 0:
        raise ValueError("unit price must be >= 0 and quantity > 0")
    subtotal = unit_cents * quantity
    discount = percent_half_up(subtotal, BULK_DISCOUNT_PERCENT) if quantity >= BULK_MIN_QUANTITY else 0
    return {
        "unit_cents": unit_cents,
        "subtotal_cents": subtotal,
        "discount_cents": discount,
        "total_cents": subtotal - discount,
    }


def format_cents(cents: int | None) -> str:
    if cents is None:
        return "—"
    return f"${cents // 100:,}.{cents % 100:02d}"
