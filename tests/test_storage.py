import sqlite3

import pytest

from order_intake.storage import Store

pytestmark = pytest.mark.unit


def test_existing_database_gains_the_llm_call_id_column_without_losing_rows(tmp_path):
    db = tmp_path / "old.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE tool_calls (id INTEGER PRIMARY KEY AUTOINCREMENT, request_id TEXT NOT NULL, "
                 "step INTEGER NOT NULL, name TEXT NOT NULL, arguments_json TEXT, result_json TEXT, "
                 "created_at TEXT NOT NULL)")
    conn.execute("INSERT INTO tool_calls (request_id, step, name, arguments_json, result_json, created_at) "
                 "VALUES ('R1', 1, 'search_catalog', ?, 'null', 'then')", ('{"query": "x"}',))
    conn.commit()
    conn.close()

    store = Store(db)
    store.add_tool_call("R1", 2, "submit_order_draft", {"lines": []}, None, llm_call_id="R1-s2-abc")
    rows = store.tool_calls("R1")
    store.close()
    assert [(r["name"], r["arguments"], r["llm_call_id"]) for r in rows] == [
        ("search_catalog", {"query": "x"}, None), ("submit_order_draft", {"lines": []}, "R1-s2-abc")]
    assert Store(db).tool_calls("R1")[1]["llm_call_id"] == "R1-s2-abc"
