from order_intake.config import ROOT
from order_intake.domain.catalog import Catalog

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
