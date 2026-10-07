import math
from fractions import Fraction

import pytest
from hypothesis import given
from hypothesis import strategies as st

from order_intake.domain.pricing import percent_half_up, price_line

pytestmark = pytest.mark.unit

UNIT_CENTS = st.integers(min_value=1, max_value=10**6)


def expected_discount(subtotal: int) -> int:
    return math.floor(Fraction(subtotal) * Fraction(10, 100) + Fraction(1, 2))


def test_worked_example_from_domain():
    assert price_line(2000, 2)["total_cents"] == 4000
    assert price_line(2000, 10) == {"unit_cents": 2000, "subtotal_cents": 20000, "discount_cents": 2000,
                                    "total_cents": 18000}


@pytest.mark.parametrize("quantity, discount, total", [(9, 0, 27000), (10, 3000, 27000), (11, 3300, 29700)])
def test_discount_boundary_around_ten_items(quantity, discount, total):
    line = price_line(3000, quantity)
    assert (line["discount_cents"], line["total_cents"]) == (discount, total)


@pytest.mark.parametrize("subtotal, expected", [(1005, 101), (1004, 100), (1015, 102), (995, 100), (5, 1), (4, 0)])
def test_ten_percent_rounds_half_up(subtotal, expected):
    assert percent_half_up(subtotal, 10) == expected


def test_rounding_inside_a_line():
    # synthetic price: the supplied catalog never produces fractional cents
    assert price_line(1005, 10) == {"unit_cents": 1005, "subtotal_cents": 10050, "discount_cents": 1005,
                                    "total_cents": 9045}
    assert price_line(1001, 15)["discount_cents"] == 1502  # 1501.5 -> 1502


@pytest.mark.parametrize("quantity", [0, -1])
def test_price_line_rejects_non_positive_quantity(quantity):
    with pytest.raises(ValueError):
        price_line(2000, quantity)


@given(unit_cents=UNIT_CENTS, quantity=st.integers(min_value=1, max_value=10**4))
def test_total_is_subtotal_minus_discount(unit_cents, quantity):
    line = price_line(unit_cents, quantity)
    assert line["subtotal_cents"] == unit_cents * quantity
    assert line["total_cents"] == line["subtotal_cents"] - line["discount_cents"]


@given(unit_cents=UNIT_CENTS, quantity=st.integers(min_value=1, max_value=9))
def test_no_discount_below_ten_items(unit_cents, quantity):
    assert price_line(unit_cents, quantity)["discount_cents"] == 0


@given(unit_cents=UNIT_CENTS, quantity=st.integers(min_value=10, max_value=10**4))
def test_discount_is_ten_percent_rounded_half_up(unit_cents, quantity):
    assert price_line(unit_cents, quantity)["discount_cents"] == expected_discount(unit_cents * quantity)
