from pathlib import Path

import pytest

from order_intake.catalog import Catalog
from order_intake.config import ROOT
from order_intake.inbox import UnusableInput, list_request_files, parse_request_file
from order_intake.pricing import percent_half_up, price_line
from order_intake.validation import NEEDS_CLARIFICATION, READY, validate_lines

CATALOG = Catalog.load(ROOT / "data" / "catalog.json")


def model_line(**overrides):
    line = {
        "product_text": "CAB-1 cables", "product_status": "matched", "sku": "CAB-1", "candidate_skus": ["CAB-1"],
        "quantity_text": "2", "quantity_status": "explicit", "quantity": 2, "unit": "item",
    }
    line.update(overrides)
    return line


def codes(result):
    return {f["code"] for f in result["findings"]}


# pricing --------------------------------------------------------------------

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


# catalog --------------------------------------------------------------------

def test_search_by_sku_inside_phrase():
    assert [r["sku"] for r in CATALOG.search("CAB-1 cables")] == ["CAB-1"]


def test_search_description_ranks_specific_match_first():
    results = CATALOG.search("USB-C cable 2 m")
    assert results[0]["sku"] == "CAB-2"
    assert CATALOG.description_candidates("USB-C cable 2 m") == ["CAB-2"]


def test_generic_cable_is_ambiguous():
    assert CATALOG.description_candidates("the usual cable") == ["CAB-1", "CAB-2"]
    assert CATALOG.description_candidates("USB-C cables") == ["CAB-1", "CAB-2"]


def test_unknown_product_has_no_candidates():
    assert CATALOG.search("Moon adapter") == []


def test_usb_hub_is_not_confused_with_usb_c_cable():
    assert CATALOG.description_candidates("USB hubs") == ["HUB-1"]


# validation -----------------------------------------------------------------

def test_valid_model_line_is_priced():
    result = validate_lines([model_line()], CATALOG, author="model", looked_up_skus={"CAB-1"},
                            source_text="Please send 2 individual CAB-1 cables.")
    assert result["status"] == READY
    assert result["order_total_cents"] == 4000
    assert result["findings"] == []


def test_sku_not_returned_by_lookup_is_blocked():
    result = validate_lines([model_line()], CATALOG, author="model", looked_up_skus=set(),
                            source_text="Please send 2 individual CAB-1 cables.")
    assert "SKU_NOT_FROM_LOOKUP" in codes(result)
    assert result["status"] == NEEDS_CLARIFICATION
    assert result["order_total_cents"] is None


def test_invented_sku_is_blocked():
    result = validate_lines([model_line(sku="CAB-9")], CATALOG, author="model", looked_up_skus={"CAB-9"},
                            source_text="Please send 2 individual CAB-1 cables.")
    assert "SKU_NOT_IN_CATALOG" in codes(result)


def test_code_catches_model_guessing_between_two_cables():
    line = model_line(product_text="USB-C cables", quantity_text="10", quantity=10)
    result = validate_lines([line], CATALOG, author="model", looked_up_skus={"CAB-1", "CAB-2"},
                            source_text="Please send 10 USB-C cables for the new office.")
    assert "AMBIGUOUS_PRODUCT" in codes(result)
    assert result["status"] == NEEDS_CLARIFICATION


def test_sku_that_contradicts_description_is_blocked():
    line = model_line(product_text="USB hubs", sku="CAB-1")
    result = validate_lines([line], CATALOG, author="model", looked_up_skus={"CAB-1", "HUB-1"},
                            source_text="Send 2 USB hubs")
    assert "PRODUCT_MISMATCH" in codes(result)


def test_boxes_are_not_converted_to_items():
    line = model_line(product_status="ambiguous", sku=None, product_text="the usual cable",
                      candidate_skus=["CAB-1", "CAB-2"], quantity_text="two boxes", quantity=2, unit="container")
    result = validate_lines([line], CATALOG, author="model", looked_up_skus={"CAB-1", "CAB-2"},
                            source_text="Send two boxes of the usual cable.")
    assert {"AMBIGUOUS_PRODUCT", "NON_ITEM_UNIT"} <= codes(result)
    assert result["lines"][0]["quantity"] is None


def test_quantity_must_be_stated_in_request():
    line = model_line(quantity_text="2", quantity=20)
    result = validate_lines([line], CATALOG, author="model", looked_up_skus={"CAB-1"},
                            source_text="Please send 2 individual CAB-1 cables.")
    assert "QUANTITY_NOT_IN_SOURCE" in codes(result)


def test_number_words_count_as_explicit():
    line = model_line(product_text="USB hubs", sku="HUB-1", quantity_text="twelve", quantity=12)
    result = validate_lines([line], CATALOG, author="model", looked_up_skus={"HUB-1"},
                            source_text="Please ship twelve USB hubs.")
    assert result["status"] == READY
    assert result["order_total_cents"] == 54000


def test_same_sku_on_two_lines_is_flagged_not_merged():
    lines = [model_line(quantity_text="5", quantity=5), model_line(quantity_text="5", quantity=5)]
    result = validate_lines(lines, CATALOG, author="model", looked_up_skus={"CAB-1"},
                            source_text="5 CAB-1 cables and another 5 CAB-1 cables")
    assert "DUPLICATE_SKU_LINES" in codes(result)


def test_reviewer_line_skips_lookup_provenance_but_checks_catalog():
    ok = validate_lines([{"sku": "CAB-1", "quantity": 10}], CATALOG, author="reviewer")
    assert ok["status"] == READY and ok["order_total_cents"] == 18000
    bad = validate_lines([{"sku": "XYZ", "quantity": 0}], CATALOG, author="reviewer")
    assert {"SKU_NOT_IN_CATALOG", "INVALID_QUANTITY"} <= codes(bad)


def test_no_lines_needs_clarification():
    result = validate_lines([], CATALOG, author="model", looked_up_skus=set(), source_text="Hello")
    assert codes(result) == {"NO_LINES"}


# inbox ----------------------------------------------------------------------

def test_all_request_files_load_except_the_unusable_one():
    files = list_request_files(ROOT / "data" / "requests")
    assert [f.stem for f in files][:5] == ["R1", "R2", "R3", "R4", "R5"]
    loaded, broken = [], []
    for f in files:
        try:
            loaded.append(parse_request_file(f, ROOT))
        except UnusableInput:
            broken.append(f.stem)
    assert broken == ["R12"]
    by_id = {r.request_id: r for r in loaded}
    assert by_id["R1"].body_hash == by_id["R4"].body_hash
    assert by_id["R5"].order_ref == by_id["R9"].order_ref and by_id["R5"].body_hash != by_id["R9"].body_hash
    assert by_id["R11"].attachment_path.endswith("R11-order-form.png")


def test_attachment_outside_folder_is_rejected(tmp_path: Path):
    (tmp_path / "X1.txt").write_text("Request-ID: X1\nOrder-Ref: OX\nAttachment: ../../etc/passwd\n\nHi\n")
    with pytest.raises(UnusableInput, match="outside"):
        parse_request_file(tmp_path / "X1.txt", tmp_path)
