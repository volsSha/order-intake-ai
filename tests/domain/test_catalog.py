from tests.domain.helpers import CATALOG


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
