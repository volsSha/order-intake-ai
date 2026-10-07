"""SQLite persistence: requests, orders (one per order reference), versioned proposals, LLM calls, audit."""

import json
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS requests (
    request_id TEXT PRIMARY KEY,
    order_ref TEXT,
    source_path TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    body_hash TEXT,
    raw_text TEXT NOT NULL,
    body TEXT,
    attachment_path TEXT,
    status TEXT NOT NULL,
    status_reason TEXT,
    related_request_id TEXT,
    current_version INTEGER,
    first_seen_at TEXT NOT NULL,
    processed_at TEXT
);
CREATE TABLE IF NOT EXISTS orders (
    order_ref TEXT PRIMARY KEY,
    request_id TEXT NOT NULL UNIQUE REFERENCES requests(request_id),
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS proposals (
    request_id TEXT NOT NULL REFERENCES requests(request_id),
    version INTEGER NOT NULL,
    author TEXT NOT NULL,
    created_at TEXT NOT NULL,
    input_lines_json TEXT NOT NULL,
    result_json TEXT NOT NULL,
    status TEXT NOT NULL,
    order_total_cents INTEGER,
    clarification TEXT,
    note TEXT,
    llm_call_ids_json TEXT,
    PRIMARY KEY (request_id, version)
);
CREATE TABLE IF NOT EXISTS llm_calls (
    call_id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL,
    step INTEGER NOT NULL,
    model TEXT,
    source TEXT NOT NULL,
    replay_file TEXT,
    created_at TEXT NOT NULL,
    latency_ms INTEGER,
    usage_json TEXT,
    error TEXT
);
CREATE TABLE IF NOT EXISTS tool_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT NOT NULL,
    step INTEGER NOT NULL,
    name TEXT NOT NULL,
    arguments_json TEXT,
    result_json TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT,
    at TEXT NOT NULL,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    detail_json TEXT
);
"""


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _loads(value):
    return json.loads(value) if value else None


class Store:
    def __init__(self, db_path: Path):
        db_path = Path(db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        self.conn.close()

    # requests -------------------------------------------------------------
    def get_request(self, request_id: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM requests WHERE request_id = ?", (request_id,)).fetchone()
        return dict(row) if row else None

    def list_requests(self, status: str | None = None) -> list[dict]:
        sql = "SELECT * FROM requests"
        args: tuple = ()
        if status:
            sql += " WHERE status = ?"
            args = (status,)
        rows = self.conn.execute(sql, args).fetchall()
        return sorted((dict(r) for r in rows), key=lambda r: _id_key(r["request_id"]))

    def save_request(self, **fields) -> None:
        existing = self.get_request(fields["request_id"])
        if existing:
            sets = ", ".join(f"{k} = ?" for k in fields if k != "request_id")
            self.conn.execute(f"UPDATE requests SET {sets} WHERE request_id = ?",
                              (*[v for k, v in fields.items() if k != "request_id"], fields["request_id"]))
        else:
            fields.setdefault("first_seen_at", now())
            cols = ", ".join(fields)
            marks = ", ".join("?" for _ in fields)
            self.conn.execute(f"INSERT INTO requests ({cols}) VALUES ({marks})", tuple(fields.values()))
        self.conn.commit()

    def set_status(self, request_id: str, status: str, reason: str | None = None,
                   related_request_id: str | None = None) -> None:
        self.conn.execute(
            "UPDATE requests SET status = ?, status_reason = ?, related_request_id = ?, processed_at = ? "
            "WHERE request_id = ?",
            (status, reason, related_request_id, now(), request_id),
        )
        self.conn.commit()

    # orders ---------------------------------------------------------------
    def order_owner(self, order_ref: str) -> str | None:
        row = self.conn.execute("SELECT request_id FROM orders WHERE order_ref = ?", (order_ref,)).fetchone()
        return row["request_id"] if row else None

    def create_order(self, order_ref: str, request_id: str) -> None:
        self.conn.execute("INSERT INTO orders (order_ref, request_id, created_at) VALUES (?, ?, ?)",
                          (order_ref, request_id, now()))
        self.conn.commit()

    def order_count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]

    # proposals ------------------------------------------------------------
    def add_proposal(self, request_id: str, *, author: str, input_lines: list[dict], result: dict,
                     clarification: str | None, note: str | None = None,
                     llm_call_ids: list[str] | None = None, status: str | None = None) -> int:
        version = (self.conn.execute("SELECT MAX(version) FROM proposals WHERE request_id = ?",
                                     (request_id,)).fetchone()[0] or 0) + 1
        status = status or result["status"]
        self.conn.execute(
            "INSERT INTO proposals (request_id, version, author, created_at, input_lines_json, result_json, status, "
            "order_total_cents, clarification, note, llm_call_ids_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (request_id, version, author, now(), json.dumps(input_lines), json.dumps(result), status,
             result.get("order_total_cents"), clarification, note, json.dumps(llm_call_ids or [])),
        )
        self.conn.execute("UPDATE requests SET current_version = ? WHERE request_id = ?", (version, request_id))
        self.conn.commit()
        return version

    def proposals(self, request_id: str) -> list[dict]:
        rows = self.conn.execute("SELECT * FROM proposals WHERE request_id = ? ORDER BY version",
                                 (request_id,)).fetchall()
        return [self._proposal(r) for r in rows]

    def latest_proposal(self, request_id: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM proposals WHERE request_id = ? ORDER BY version DESC LIMIT 1",
                                (request_id,)).fetchone()
        return self._proposal(row) if row else None

    def set_proposal_status(self, request_id: str, version: int, status: str) -> None:
        self.conn.execute("UPDATE proposals SET status = ? WHERE request_id = ? AND version = ?",
                          (status, request_id, version))
        self.conn.commit()

    @staticmethod
    def _proposal(row) -> dict:
        d = dict(row)
        d["input_lines"] = _loads(d.pop("input_lines_json"))
        d["result"] = _loads(d.pop("result_json"))
        d["llm_call_ids"] = _loads(d.pop("llm_call_ids_json")) or []
        return d

    # model and tool calls -------------------------------------------------
    def add_llm_call(self, record: dict) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO llm_calls (call_id, request_id, step, model, source, replay_file, created_at, "
            "latency_ms, usage_json, error) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (record["call_id"], record["request_id"], record["step"], record.get("model"), record["source"],
             record.get("replay_file"), now(), record.get("latency_ms"), json.dumps(record.get("usage")),
             record.get("error")),
        )
        self.conn.commit()

    def llm_calls(self, request_id: str) -> list[dict]:
        rows = self.conn.execute("SELECT * FROM llm_calls WHERE request_id = ? ORDER BY created_at, step",
                                 (request_id,)).fetchall()
        return [{**dict(r), "usage": _loads(r["usage_json"])} for r in rows]

    def add_tool_call(self, request_id: str, step: int, name: str, arguments, result) -> None:
        self.conn.execute(
            "INSERT INTO tool_calls (request_id, step, name, arguments_json, result_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (request_id, step, name, json.dumps(arguments), json.dumps(result), now()),
        )
        self.conn.commit()

    def tool_calls(self, request_id: str) -> list[dict]:
        rows = self.conn.execute("SELECT * FROM tool_calls WHERE request_id = ? ORDER BY id", (request_id,)).fetchall()
        return [{**dict(r), "arguments": _loads(r["arguments_json"]), "result": _loads(r["result_json"])} for r in rows]

    def clear_attempt(self, request_id: str) -> None:
        """Drop tool-call rows from a previous failed attempt so evidence matches the saved proposal."""
        self.conn.execute("DELETE FROM tool_calls WHERE request_id = ?", (request_id,))
        self.conn.commit()

    # audit ----------------------------------------------------------------
    def audit(self, request_id: str | None, actor: str, action: str, detail: dict | None = None) -> None:
        self.conn.execute("INSERT INTO audit_log (request_id, at, actor, action, detail_json) VALUES (?, ?, ?, ?, ?)",
                          (request_id, now(), actor, action, json.dumps(detail or {})))
        self.conn.commit()

    def audit_entries(self, request_id: str) -> list[dict]:
        rows = self.conn.execute("SELECT * FROM audit_log WHERE request_id = ? ORDER BY id", (request_id,)).fetchall()
        return [{**dict(r), "detail": _loads(r["detail_json"])} for r in rows]

    # analytics ------------------------------------------------------------
    def status_counts(self) -> dict[str, int]:
        rows = self.conn.execute("SELECT status, COUNT(*) AS n FROM requests GROUP BY status").fetchall()
        return {r["status"]: r["n"] for r in rows}


def _id_key(request_id: str) -> tuple:
    return tuple(int(p) if p.isdigit() else p for p in re.split(r"(\d+)", request_id))
