import pytest
from fastapi.testclient import TestClient

from order_intake.web.app import create_app

pytestmark = pytest.mark.integration


def client_for(settings, make_pipeline, only=None):
    p = make_pipeline()
    p.process_all(only=only)
    p.store.close()
    return TestClient(create_app(settings))


def test_dashboard_counts_and_improvement(settings, make_pipeline):
    with client_for(settings, make_pipeline) as c:
        html = c.get("/").text
    assert "Requests received" in html
    assert "Suggested intake improvement" in html
    assert "R4</a> repeats" in html


def test_queue_filter_returns_only_matching_rows(settings, make_pipeline):
    with client_for(settings, make_pipeline) as c:
        rows = c.get("/queue?status=duplicate", headers={"HX-Request": "true"}).text
        full = c.get("/queue").text
    assert rows.count("<tr>") == 1 and "/requests/R4\">R4</a>" in rows
    assert "<html" not in rows and "<html" in full


def test_detail_shows_request_beside_proposal_and_lookups(settings, make_pipeline):
    with client_for(settings, make_pipeline, only={"R6"}) as c:
        html = c.get("/requests/R6").text
    assert "3 x HUB-1" in html and "$420.00" in html
    assert "Catalog lookups" in html and "search “USB-C cable 2 m”" in html


def test_correction_via_form_then_approve_then_export(settings, make_pipeline):
    with client_for(settings, make_pipeline, only={"R7"}) as c:
        r = c.post("/requests/R7/correct", data={"sku_0": "CAB-1", "qty_0": "10", "text_0": "USB-C cables",
                                                 "note": "Customer confirmed 1 m", "reviewer": "ana"})
        assert r.status_code == 200
        html = c.get("/requests/R7").text
        assert "Model proposal vs current version" in html and "$180.00" in html
        assert "Customer confirmed 1 m" in html
        c.post("/requests/R7/approve", data={"reviewer": "ana"})
        exported = c.get("/export/approved.json").json()
        csv_text = c.get("/export/approved.csv").text
    assert exported["orders"][0]["order_total_cents"] == 18000
    assert exported["orders"][0]["approved_by"] == "ana"
    assert "O6,R7,2,ana" in csv_text


def test_invalid_correction_is_saved_as_needing_clarification(settings, make_pipeline):
    with client_for(settings, make_pipeline, only={"R1"}) as c:
        c.post("/requests/R1/correct", data={"sku_0": "", "qty_0": "0"})
        html = c.get("/requests/R1").text
    assert "SKU_NOT_IN_CATALOG" in html and "INVALID_QUANTITY" in html


def test_attachment_route_blocks_path_traversal(settings, make_pipeline):
    with client_for(settings, make_pipeline, only={"R11"}) as c:
        assert c.get("/attachments/R11-order-form.png").status_code == 200
        assert c.get("/attachments/..%2F..%2Fcatalog.json").status_code == 404


def test_rejected_action_shows_full_error_message(settings, make_pipeline):
    with client_for(settings, make_pipeline, only={"R2"}) as c:
        r = c.post("/requests/R2/approve", data={"reviewer": "ana"}, follow_redirects=False)
        assert "%20" in r.headers["location"]
        html = c.get(r.headers["location"]).text
    assert "Only a draft that passed validation (ready_for_review) can be approved." in html


def test_model_calls_show_source_provider_tokens_and_cost(settings, make_pipeline):
    from order_intake.storage import Store

    p = make_pipeline()
    p.process_all(only={"R1"})
    p.store.close()
    store = Store(settings.db_path)
    store.add_llm_call({"call_id": "R1-s9-x", "request_id": "R1", "step": 9, "model": "openai/gpt-6-luna",
                        "provider": "openrouter", "source": "replay", "replay_file": "replay/extract/R1/x.json",
                        "usage": {"input_tokens": 1000, "output_tokens": 234, "total_tokens": 1234, "cost": 0.0021}})
    store.close()
    with TestClient(create_app(settings)) as c:
        detail = c.get("/requests/R1").text
        dashboard = c.get("/").text
    assert 'badge src-live">live' in detail and 'badge src-replay">replay' in detail
    assert "openai/gpt-6-luna · scripted" in detail and "openai/gpt-6-luna · openrouter" in detail
    assert ">1234<" in detail
    assert "Cost (recorded)" in dashboard and "$0.0021" in dashboard
