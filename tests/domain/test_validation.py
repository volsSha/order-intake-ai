import pytest

from order_intake.domain.validation import NEEDS_CLARIFICATION, READY, numbers_in, validate_lines
from tests.domain.helpers import CATALOG, codes, model_line

pytestmark = pytest.mark.unit


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


def test_unit_is_kept_on_validated_lines():
    model = validate_lines([model_line(unit="item")], CATALOG, author="model", looked_up_skus={"CAB-1"},
                           source_text="2 CAB-1 cables")
    reviewer = validate_lines([{"sku": "CAB-1", "quantity": 2}], CATALOG, author="reviewer")
    assert model["lines"][0]["unit"] == "item" and reviewer["lines"][0]["unit"] == "item"


def validate_model(line, source_text, looked_up=("CAB-1",)):
    return validate_lines([line], CATALOG, author="model", looked_up_skus=set(looked_up), source_text=source_text)


@pytest.mark.parametrize("quantity, discount, total", [(9, 0, 18000), (10, 2000, 18000), (11, 2200, 19800)])
def test_validated_line_discount_boundary(quantity, discount, total):
    result = validate_model(model_line(quantity_text=str(quantity), quantity=quantity),
                            f"Please send {quantity} CAB-1 cables.")
    assert result["status"] == READY
    assert result["lines"][0]["discount_cents"] == discount
    assert result["order_total_cents"] == total


@pytest.mark.parametrize("text, expected", [
    ("one", {1}), ("two", {2}), ("nine", {9}), ("ten", {10}), ("Twelve", {12}), ("twenty", {20}),
    ("12 x", {12}), ("3 or 4", {3, 4}), ("a few", set()), ("dozens", set()),
    ("a dozen", {12}), ("dozen", {12}), ("half a dozen", {6}), ("two dozen", {24}), ("3 dozen", {36}),
])
def test_numbers_in_reads_digits_and_number_words(text, expected):
    assert numbers_in(text) == expected


@pytest.mark.parametrize("word, quantity", [("two", 2), ("nine", 9), ("ten", 10), ("eleven", 11), ("twelve", 12)])
def test_number_word_quantity_is_accepted(word, quantity):
    result = validate_model(model_line(quantity_text=word, quantity=quantity), f"Send {word} CAB-1 cables.")
    assert result["status"] == READY
    assert result["lines"][0]["quantity"] == quantity


@pytest.mark.parametrize("word, quantity", [("two", 3), ("twelve", 2), ("ten", 100)])
def test_number_word_that_disagrees_with_quantity_is_blocked(word, quantity):
    result = validate_model(model_line(quantity_text=word, quantity=quantity), f"Send {word} CAB-1 cables.")
    assert codes(result) == {"QUANTITY_NOT_IN_SOURCE"}


def test_compound_number_word_does_not_accept_a_part():
    result = validate_model(model_line(quantity_text="twenty-one", quantity=1), "Send twenty-one CAB-1 cables.")
    assert "QUANTITY_NOT_IN_SOURCE" in codes(result)


@pytest.mark.parametrize("qty_text", ["two boxes", "1 box", "3 packs", "a pack", "one case", "2 cartons"])
def test_container_quantity_is_not_priced(qty_text):
    line = model_line(quantity_text=qty_text, quantity=None, quantity_status="ambiguous", unit="container")
    result = validate_model(line, f"Send {qty_text} of CAB-1 cables.")
    assert codes(result) == {"NON_ITEM_UNIT"}
    assert result["lines"][0]["quantity"] is None
    assert result["order_total_cents"] is None


def test_container_words_labelled_as_items_are_still_blocked():
    result = validate_model(model_line(quantity_text="2 boxes", quantity=2), "Send 2 boxes of CAB-1 cables.")
    assert result["status"] == NEEDS_CLARIFICATION
    assert [(f["code"], f["source"]) for f in result["findings"]] == [("NON_ITEM_UNIT", "code")]
    assert result["order_total_cents"] is None


def test_compound_number_word_matches_its_full_value():
    result = validate_model(model_line(quantity_text="twenty-one", quantity=21), "Send twenty-one CAB-1 cables.")
    assert result["status"] == READY and result["lines"][0]["quantity"] == 21


@pytest.mark.parametrize("qty_text", ["a few", "some", "several", "enough"])
def test_vague_amount_needs_clarification(qty_text):
    line = model_line(quantity_text=qty_text, quantity=None, quantity_status="ambiguous", unit="unclear")
    result = validate_model(line, f"Send {qty_text} CAB-1 cables.")
    assert codes(result) == {"AMBIGUOUS_QUANTITY"}
    assert result["status"] == NEEDS_CLARIFICATION
    assert result["lines"][0]["quantity"] is None


@pytest.mark.parametrize("qty_text, guess", [("a few", 3), ("some", 2), ("several", 5), ("enough", 10)])
def test_vague_amount_with_guessed_number_is_blocked(qty_text, guess):
    result = validate_model(model_line(quantity_text=qty_text, quantity=guess), f"Send {qty_text} CAB-1 cables.")
    assert codes(result) == {"QUANTITY_NOT_IN_SOURCE"}
    assert result["order_total_cents"] is None


J1 = "Could we get a dozen of the two-metre USB-C cables?"


def test_dozen_of_the_two_metre_cables_is_ready_not_ambiguous():
    line = model_line(product_text="two-metre USB-C cables", sku="CAB-2", quantity_text="a dozen", quantity=12)
    result = validate_model(line, J1, looked_up=("CAB-1", "CAB-2"))
    assert result["findings"] == []
    assert result["status"] == READY
    assert result["order_total_cents"] == 12 * 3000 - 3600


def test_two_dozen_does_not_support_a_quantity_of_twelve():
    line = model_line(quantity_text="two dozen", quantity=12)
    assert "QUANTITY_NOT_IN_SOURCE" in codes(validate_model(line, "Send two dozen CAB-1 cables."))
