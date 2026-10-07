import pytest

from order_intake.domain.validation import clarification_message, validate_lines
from tests.domain.helpers import CATALOG, model_line

pytestmark = pytest.mark.unit


def test_model_clarification_gets_order_reference_frame():
    lines = [model_line(product_text="Moon adapter", product_status="unknown", sku=None, candidate_skus=[])]
    result = validate_lines(lines, CATALOG, author="model", looked_up_skus=set(), source_text="one Moon adapter")
    msg = clarification_message("O2", result["findings"], lines, CATALOG, "- Moon adapter is not in our catalog.")
    assert msg.startswith("Hello,") and "order O2" in msg
    assert "- Moon adapter is not in our catalog." in msg and msg.endswith("Order desk")


def test_code_findings_use_template_not_model_draft():
    lines = [model_line(product_text="USB-C cables", sku="CAB-1", quantity_text="10", quantity=10)]
    result = validate_lines(lines, CATALOG, author="model", looked_up_skus={"CAB-1", "CAB-2"},
                            source_text="10 USB-C cables")
    msg = clarification_message("O6", result["findings"], lines, CATALOG, "- anything")
    assert "anything" not in msg and "CAB-1" in msg and "CAB-2" in msg
