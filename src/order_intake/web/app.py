"""Review web app: dashboard, operations queue, request detail with correction and approval."""

import csv
import io
import json
import threading
from collections import Counter
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ..config import Settings, load_settings
from ..domain.pricing import format_cents
from ..domain.validation import APPROVED, FINDING_LABELS, STATUSES
from ..pipeline import Pipeline, ReviewError
from ..storage import Store

HERE = Path(__file__).parent
templates = Jinja2Templates(directory=HERE / "templates")
templates.env.filters["money"] = format_cents
templates.env.filters["label"] = lambda code: FINDING_LABELS.get(code, code)
templates.env.filters["pretty"] = lambda v: json.dumps(v, indent=2, ensure_ascii=False)

STATUS_LABELS = {
    "ready_for_review": "Ready for review", "needs_clarification": "Needs clarification", "approved": "Approved",
    "duplicate": "Duplicate", "failed": "Failed", "processing": "Processing",
}
templates.env.filters["status_label"] = lambda s: STATUS_LABELS.get(s, s)

IMPROVEMENTS = {
    "AMBIGUOUS_PRODUCT": "Ask customers for SKUs, or offer an order form listing catalog SKUs: generic wording "
                         "such as \"USB-C cables\" matches several products and always needs a follow-up.",
    "NON_ITEM_UNIT": "State on the order form that quantities are individual items; box sizes are not defined.",
    "AMBIGUOUS_QUANTITY": "Ask for an exact item count on the order form; vague amounts cannot be priced.",
    "UNKNOWN_PRODUCT": "Reply with a link to the catalog when a product is not recognised.",
    "CONFLICTING_ORDER_REF": "Give customers a way to amend an existing order explicitly instead of resending "
                             "with the same reference.",
}


def provider_name(settings: Settings) -> str | None:
    try:
        provider = settings.resolve_provider()
    except ValueError:
        return None
    return provider.name if provider else None


class App:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.store = Store(settings.db_path)
        self.pipeline = Pipeline(settings, self.store)
        self.lock = threading.Lock()


def create_app(settings: Settings | None = None) -> FastAPI:
    state: dict = {}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        state["app"] = App(settings or load_settings())
        yield
        state["app"].store.close()

    app = FastAPI(title="Order intake", lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")

    def ctx() -> App:
        return state["app"]

    def queue_rows(status: str | None) -> list[dict]:
        a = ctx()
        rows = []
        for r in a.store.list_requests(status or None):
            proposal = a.store.latest_proposal(r["request_id"])
            calls = a.store.llm_calls(r["request_id"])
            rows.append({**r, "total": proposal["order_total_cents"] if proposal else None,
                         "version": proposal["version"] if proposal else None,
                         "author": proposal["author"] if proposal else None,
                         "sources": sorted({c["source"] for c in calls})})
        return rows

    @app.get("/", response_class=HTMLResponse)
    def dashboard(request: Request):
        a = ctx()
        with a.lock:
            reqs = a.store.list_requests()
            counts = Counter(r["status"] for r in reqs)
            reasons: dict[str, list[str]] = {}
            for r in reqs:
                if r["status"] in ("needs_clarification", "failed") and r["status_reason"]:
                    for code in r["status_reason"].split(":")[0].split(", "):
                        reasons.setdefault(code.strip(), []).append(r["request_id"])
            duplicates = [r for r in reqs if r["status"] == "duplicate"]
            all_calls = [c for r in reqs for c in a.store.llm_calls(r["request_id"])]
            sources = Counter(c["source"] for c in all_calls)
            tokens = sum((c["usage"] or {}).get("total_tokens", 0) for c in all_calls)
            cost = sum((c["usage"] or {}).get("cost", 0) or 0 for c in all_calls)
            top = sorted(reasons.items(), key=lambda kv: -len(kv[1]))
            improvement = next(((code, ids, IMPROVEMENTS[code]) for code, ids in top if code in IMPROVEMENTS), None)
            return templates.TemplateResponse(request, "dashboard.html", {
                "counts": counts, "total": len(reqs), "orders": a.store.order_count(), "reasons": top,
                "duplicates": duplicates, "sources": sources, "tokens": tokens, "cost": cost,
                "improvement": improvement, "settings": a.settings, "provider": provider_name(a.settings),
            })

    @app.get("/queue", response_class=HTMLResponse)
    def queue(request: Request, status: str | None = None):
        a = ctx()
        with a.lock:
            rows = queue_rows(status)
            counts = a.store.status_counts()
        template = "_queue_rows.html" if request.headers.get("HX-Request") else "queue.html"
        return templates.TemplateResponse(request, template, {
            "rows": rows, "status": status or "", "statuses": STATUSES, "counts": counts})

    @app.get("/requests/{request_id}", response_class=HTMLResponse)
    def detail(request: Request, request_id: str, error: str | None = None):
        a = ctx()
        with a.lock:
            req = a.store.get_request(request_id)
            if not req:
                raise HTTPException(404, "unknown request")
            proposals = a.store.proposals(request_id)
            model_versions = [p for p in proposals if p["author"] == "model"]
            return templates.TemplateResponse(request, "detail.html", {
                "req": req, "proposals": proposals, "current": proposals[-1] if proposals else None,
                "model_version": model_versions[0] if model_versions else None,
                "calls": a.store.llm_calls(request_id), "tools": a.store.tool_calls(request_id),
                "audit": a.store.audit_entries(request_id), "catalog": a.pipeline.catalog.items,
                "can_edit": a.pipeline.can_edit(request_id), "error": error,
            })

    @app.post("/requests/{request_id}/correct")
    async def correct(request_id: str, request: Request):
        form = await request.form()
        lines = []
        indexes = sorted({k.split("_", 1)[1] for k in form if k.startswith("sku_")})
        for idx in indexes:
            if form.get(f"remove_{idx}"):
                continue
            sku = (form.get(f"sku_{idx}") or "").strip()
            qty_raw = (form.get(f"qty_{idx}") or "").strip()
            if idx == "new" and not sku and not qty_raw:
                continue
            try:
                qty = int(qty_raw) if qty_raw else None
            except ValueError:
                qty = None
            lines.append({"sku": sku or None, "quantity": qty, "product_text": form.get(f"text_{idx}") or "",
                          "quantity_text": qty_raw})
        a = ctx()

        def save():
            with a.lock:
                a.pipeline.correct(request_id, lines, note=str(form.get("note") or ""),
                                   reviewer=str(form.get("reviewer") or "reviewer"))

        # Sync DB work and the shared lock must not block the event loop.
        try:
            await run_in_threadpool(save)
        except ReviewError as exc:
            return RedirectResponse(f"/requests/{request_id}?error={quote(str(exc))}", status_code=303)
        return RedirectResponse(f"/requests/{request_id}#current", status_code=303)

    @app.post("/requests/{request_id}/approve")
    def approve(request_id: str, note: str = Form(""), reviewer: str = Form("reviewer")):
        a = ctx()
        with a.lock:
            try:
                a.pipeline.approve(request_id, note=note, reviewer=reviewer or "reviewer")
            except ReviewError as exc:
                return RedirectResponse(f"/requests/{request_id}?error={quote(str(exc))}", status_code=303)
        return RedirectResponse(f"/requests/{request_id}", status_code=303)

    @app.post("/process")
    def process(retry_failed: bool = Form(False)):
        a = ctx()
        with a.lock:
            a.pipeline.process_all(retry_failed=retry_failed)
        return RedirectResponse("/queue", status_code=303)

    @app.get("/attachments/{name}")
    def attachment(name: str):
        base = (ctx().settings.requests_dir / "attachments").resolve()
        path = (base / name).resolve()
        if base not in path.parents or not path.is_file():
            raise HTTPException(404)
        return FileResponse(path)

    def approved_orders() -> list[dict]:
        a = ctx()
        out = []
        for r in a.store.list_requests(APPROVED):
            p = a.store.latest_proposal(r["request_id"])
            approval = next((e for e in reversed(a.store.audit_entries(r["request_id"])) if e["action"] == "approved"),
                            None)
            out.append({
                "request_id": r["request_id"], "order_ref": r["order_ref"], "version": p["version"],
                "approved_by": approval["actor"] if approval else None, "approved_at": approval["at"] if approval else None,
                "lines": [{k: line.get(k) for k in ("sku", "name", "quantity", "unit_cents", "subtotal_cents",
                                                     "discount_cents", "total_cents")} for line in p["result"]["lines"]],
                "order_total_cents": p["order_total_cents"],
            })
        return out

    @app.get("/export/approved.json")
    def export_json():
        with ctx().lock:
            data = approved_orders()
        return JSONResponse({"currency": "USD", "amounts": "integer cents", "orders": data},
                            headers={"Content-Disposition": "attachment; filename=approved-orders.json"})

    @app.get("/export/approved.csv")
    def export_csv():
        with ctx().lock:
            data = approved_orders()
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["order_ref", "request_id", "version", "approved_by", "approved_at", "sku", "quantity",
                         "unit_cents", "subtotal_cents", "discount_cents", "line_total_cents", "order_total_cents"])
        for o in data:
            for line in o["lines"]:
                writer.writerow([o["order_ref"], o["request_id"], o["version"], o["approved_by"], o["approved_at"],
                                 line["sku"], line["quantity"], line["unit_cents"], line["subtotal_cents"],
                                 line["discount_cents"], line["total_cents"], o["order_total_cents"]])
        return Response(buf.getvalue(), media_type="text/csv",
                        headers={"Content-Disposition": "attachment; filename=approved-orders.csv"})

    return app


app = create_app()
