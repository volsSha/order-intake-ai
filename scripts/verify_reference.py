"""Independently re-check the arithmetic in data/reference/expected.json.

Deliberately imports nothing from the application: it re-implements the two pricing
rules from domain.md in the simplest possible way so a bug in src/ cannot hide here.
"""

import json
import sys
from fractions import Fraction
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def half_up(value: Fraction) -> int:
    return int(value + Fraction(1, 2)) if value >= 0 else -int(-value + Fraction(1, 2))


def check_line(line: dict, catalog: dict[str, int]) -> list[str]:
    errors = []
    unit = catalog[line["sku"]]
    subtotal = unit * line["quantity"]
    discount = half_up(Fraction(subtotal, 10)) if line["quantity"] >= 10 else 0
    expected = {"unit_cents": unit, "subtotal_cents": subtotal, "discount_cents": discount,
                "total_cents": subtotal - discount}
    for key, value in expected.items():
        if line[key] != value:
            errors.append(f"{line['sku']} {key}: file says {line[key]}, recomputed {value}")
    return errors


def main() -> int:
    catalog = {c["sku"]: c["unit_cents"] for c in json.loads((ROOT / "data/catalog.json").read_text())}
    ref = json.loads((ROOT / "data/reference/expected.json").read_text())
    seed_expected = {r["id"]: r for r in json.loads((ROOT / "starter/orders/expected-seed-results.json").read_text())}
    errors: list[str] = []
    checked = 0
    for case in ref["cases"]:
        exp = case["expected"]
        for lines_key, total_key in (("lines", "order_total_cents"), ("lines_after", "order_total_after_cents")):
            if lines_key in exp:
                for line in exp[lines_key]:
                    errors += [f"{case['case_id']}: {e}" for e in check_line(line, catalog)]
                total = sum(line["total_cents"] for line in exp[lines_key])
                if total != exp[total_key]:
                    errors.append(f"{case['case_id']}: {total_key} {exp[total_key]} != sum of lines {total}")
                checked += 1
        seed = seed_expected.get(case["request_id"])
        if seed and seed.get("total_cents") is not None and exp.get("order_total_cents") != seed["total_cents"]:
            errors.append(f"{case['case_id']}: disagrees with starter seed total {seed['total_cents']}")
    # domain.md worked example: 2 x CAB-1 = 4000; 10 x CAB-1 = 18000 after 2000 discount
    for qty, total in ((2, 4000), (10, 18000)):
        line = {"sku": "CAB-1", "quantity": qty, "unit_cents": 2000, "subtotal_cents": 2000 * qty,
                "discount_cents": 2000 * qty - total, "total_cents": total}
        errors += [f"worked example: {e}" for e in check_line(line, catalog)]
    counts = ref["batch"]["status_counts_before_review"]
    if sum(counts.values()) != len(list((ROOT / "data/requests").glob("R*.txt"))):
        errors.append("batch status counts do not add up to the number of request files")
    for e in errors:
        print("FAIL", e)
    print(f"checked {checked} priced cases + worked example: {'OK' if not errors else f'{len(errors)} error(s)'}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
