import pytest

from order_intake.domain.pricing import percent_half_up, price_line


def test_worked_example_from_domain():
    assert price_line(2000, 2)["total_cents"] == 4000
    assert price_line(2000, 10) == {"unit_cents": 2000, "subtotal_cents": 20000, "discount_cents": 2000,
                                    "total_cents": 18000}


def test_discount_starts_at_ten_items():
    assert price_line(3000, 9)["discount_cents"] == 0
    assert price_line(3000, 10)["discount_cents"] == 3000


@pytest.mark.parametrize("subtotal, expected", [(1005, 101), (1004, 100), (1015, 102), (995, 100), (5, 1), (4, 0)])
def test_ten_percent_rounds_half_up(subtotal, expected):
    assert percent_half_up(subtotal, 10) == expected


def test_rounding_inside_a_line():
    # synthetic price: the supplied catalog never produces fractional cents
    assert price_line(1005, 10) == {"unit_cents": 1005, "subtotal_cents": 10050, "discount_cents": 1005,
                                    "total_cents": 9045}
    assert price_line(1001, 15)["discount_cents"] == 1502  # 1501.5 -> 1502


def test_price_line_rejects_non_positive_quantity():
    with pytest.raises(ValueError):
        price_line(2000, 0)
