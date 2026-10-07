"""Batch processing and reviewer actions. One bad request never stops the batch."""

import hashlib
from collections.abc import Callable
from pathlib import Path

from .config import Settings
from .domain.catalog import Catalog
from .domain.inbox import UnusableInput, list_request_files, parse_request_file, request_id_for
from .domain.validation import (
    APPROVED,
    DUPLICATE,
    FAILED,
    NEEDS_CLARIFICATION,
    READY,
    clarification_message,
    finding,
    validate_lines,
)
from .llm.agent import ExtractionFailed, extract
from .llm.replay import ModelFactory, ReplayModel
from .storage import Store

ModelFactoryFn = Callable[[Settings, Store, str], ReplayModel]


class ReviewError(Exception):
    pass


def reason_from(findings: list[dict]) -> str | None:
    codes = list(dict.fromkeys(f["code"] for f in findings if f["severity"] == "blocking"))
    return ", ".join(codes) or None


class Pipeline:
    def __init__(self, settings: Settings, store: Store, catalog: Catalog | None = None,
                 model_factory: ModelFactoryFn | None = None):
        self.settings = settings
        self.store = store
        self.catalog = catalog or Catalog.load(settings.catalog_path)
        self.model_factory = model_factory or ModelFactory()

    # batch ----------------------------------------------------------------
    def process_all(self, only: set[str] | None = None, retry_failed: bool = False) -> list[dict]:
        results = []
        for path in list_request_files(self.settings.requests_dir):
            if only and path.stem not in only:
                continue
            try:
                results.append(self.process_file(path, retry_failed=retry_failed))
            except Exception as exc:  # noqa: BLE001 - last-resort guard: record and keep going
                request_id = request_id_for(path, path.read_text(encoding="utf-8", errors="replace"))
                if self.store.get_request(request_id):
                    self.store.set_status(request_id, FAILED, f"PROCESSING_ERROR: {exc!r}")
                results.append({"request_id": request_id, "status": FAILED, "action": f"crashed: {exc!r}"})
        return results

    def process_file(self, path: Path, retry_failed: bool = False) -> dict:
        raw = path.read_text(encoding="utf-8", errors="replace")
        request_id = request_id_for(path, raw)
        existing = self.store.get_request(request_id)
        content_hash = hashlib.sha256(raw.encode()).hexdigest()

        if existing and existing["content_hash"] != content_hash:
            self.store.audit(request_id, "system", "rejected_changed_file", {"source_path": str(path)})
            return {"request_id": request_id, "status": existing["status"],
                    "action": "skipped: request ID already used with different content"}
        if existing and existing["status"] != "processing" and not (retry_failed and existing["status"] == FAILED):
            return {"request_id": request_id, "status": existing["status"], "action": "skipped: already processed"}

        try:
            req = parse_request_file(path, self.settings.root)
        except UnusableInput as exc:
            self.store.save_request(request_id=request_id, order_ref=None, source_path=str(path.name),
                                    content_hash=content_hash, raw_text=raw, status=FAILED,
                                    status_reason=f"UNUSABLE_INPUT: {exc}")
            self.store.audit(request_id, "system", "unusable_input", {"error": str(exc)})
            return {"request_id": request_id, "status": FAILED, "action": f"unusable input: {exc}"}

        self.store.save_request(request_id=req.request_id, order_ref=req.order_ref, source_path=req.source_path,
                                content_hash=req.content_hash, body_hash=req.body_hash, raw_text=req.raw_text,
                                body=req.body, attachment_path=req.attachment_path, status="processing")

        owner = self.store.order_owner(req.order_ref)
        if owner and owner != req.request_id:
            return self._handle_existing_ref(req, owner)
        if not owner:
            self.store.create_order(req.order_ref, req.request_id)
            self.store.audit(req.request_id, "system", "order_created", {"order_ref": req.order_ref})

        self.store.clear_attempt(req.request_id)
        try:
            model = self.model_factory(self.settings, self.store, req.request_id)
            extraction = extract(req, self.catalog, model, self.settings, self.store)
        except ExtractionFailed as exc:
            self.store.set_status(req.request_id, FAILED, f"{exc.code}: {exc}")
            self.store.audit(req.request_id, "system", "model_failed", {"code": exc.code, "error": str(exc)})
            return {"request_id": req.request_id, "status": FAILED, "action": f"{exc.code}: {exc}"}

        lines = [line.model_dump() for line in extraction.submission.lines]
        result = validate_lines(lines, self.catalog, author="model", looked_up_skus=extraction.looked_up_skus,
                                source_text=None if req.attachment_path else req.body)
        result["model_notes"] = extraction.submission.notes
        result["looked_up_skus"] = sorted(extraction.looked_up_skus)
        clarification = clarification_message(req.order_ref, result["findings"], lines, self.catalog,
                                              extraction.submission.clarification_draft)
        version = self.store.add_proposal(req.request_id, author="model", input_lines=lines, result=result,
                                          clarification=clarification, llm_call_ids=extraction.llm_call_ids)
        self.store.set_status(req.request_id, result["status"], reason_from(result["findings"]))
        self.store.audit(req.request_id, "model", "proposal_created",
                         {"version": version, "status": result["status"], "calls": extraction.llm_call_ids})
        return {"request_id": req.request_id, "status": result["status"], "action": f"proposal v{version}"}

    def _handle_existing_ref(self, req, owner: str) -> dict:
        owner_req = self.store.get_request(owner)
        if owner_req and owner_req["body_hash"] == req.body_hash:
            self.store.set_status(req.request_id, DUPLICATE,
                                  f"DUPLICATE_REQUEST: same order reference {req.order_ref} and content as {owner}",
                                  related_request_id=owner)
            self.store.audit(req.request_id, "system", "duplicate_detected", {"duplicate_of": owner})
            return {"request_id": req.request_id, "status": DUPLICATE, "action": f"duplicate of {owner}; no new draft"}

        conflict = finding("CONFLICTING_ORDER_REF",
                           f"Order reference {req.order_ref} already belongs to request {owner} with different "
                           "content. The existing draft was not changed.")
        result = {"lines": [], "findings": [conflict], "order_total_cents": None, "status": NEEDS_CLARIFICATION}
        clarification = (f"Hello,\n\nWe already have order {req.order_ref} from you with different details. "
                         "Should this message replace that order, or is it a separate order? If separate, "
                         "please send it with a new order reference.\n\nKind regards,\nOrder desk")
        self.store.add_proposal(req.request_id, author="system", input_lines=[], result=result,
                                clarification=clarification)
        self.store.set_status(req.request_id, NEEDS_CLARIFICATION, "CONFLICTING_ORDER_REF", related_request_id=owner)
        self.store.audit(req.request_id, "system", "conflicting_order_ref", {"existing_request": owner})
        return {"request_id": req.request_id, "status": NEEDS_CLARIFICATION,
                "action": f"conflicts with {owner}; no new draft"}

    # reviewer actions -----------------------------------------------------
    def can_edit(self, request_id: str) -> bool:
        req = self.store.get_request(request_id)
        return bool(req and req["order_ref"] and self.store.order_owner(req["order_ref"]) == request_id)

    def correct(self, request_id: str, lines: list[dict], note: str = "", reviewer: str = "reviewer") -> dict:
        if not self.can_edit(request_id):
            raise ReviewError("Only the request that owns an order can be corrected.")
        req = self.store.get_request(request_id)
        before = self.store.latest_proposal(request_id)
        clean = [{"sku": (line.get("sku") or "").strip().upper() or None, "quantity": line.get("quantity"),
                  "product_text": line.get("product_text") or "", "quantity_text": line.get("quantity_text") or ""}
                 for line in lines]
        result = validate_lines(clean, self.catalog, author="reviewer")
        clarification = clarification_message(req["order_ref"], result["findings"], clean, self.catalog)
        version = self.store.add_proposal(request_id, author=reviewer, input_lines=clean, result=result,
                                          clarification=clarification, note=note)
        self.store.set_status(request_id, result["status"], reason_from(result["findings"]))
        self.store.audit(request_id, reviewer, "correction", {
            "from_version": before["version"] if before else None, "to_version": version, "note": note,
            "before": [(p["sku"], p["quantity"]) for p in before["result"]["lines"]] if before else [],
            "after": [(p["sku"], p["quantity"]) for p in result["lines"]], "status": result["status"]})
        return {"version": version, "status": result["status"], "result": result}

    def approve(self, request_id: str, note: str = "", reviewer: str = "reviewer") -> None:
        latest = self.store.latest_proposal(request_id)
        if not latest or latest["status"] != READY or not self.can_edit(request_id):
            raise ReviewError("Only a draft that passed validation (ready_for_review) can be approved.")
        self.store.set_proposal_status(request_id, latest["version"], APPROVED)
        self.store.set_status(request_id, APPROVED, f"approved v{latest['version']}")
        self.store.audit(request_id, reviewer, "approved", {"version": latest["version"], "note": note})
