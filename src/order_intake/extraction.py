"""Bounded tool loop: the model may only search the catalog and submit one draft."""

import base64
import json
from dataclasses import dataclass, field

from pydantic import ValidationError

from .catalog import Catalog
from .config import Settings
from .inbox import IMAGE_TYPES, IncomingRequest
from .llm import ChatClient, LLMError
from .schemas import TOOLS, OrderDraftSubmission, SearchCatalogArgs
from .storage import Store


class ExtractionFailed(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass
class Extraction:
    submission: OrderDraftSubmission
    looked_up_skus: set[str]
    llm_call_ids: list[str] = field(default_factory=list)


def user_message(request: IncomingRequest, settings: Settings) -> dict:
    text = (f"Order request {request.request_id}, order reference {request.order_ref}.\n"
            f"<request>\n{request.body or '(empty body)'}\n</request>")
    if not request.attachment_path:
        return {"role": "user", "content": text}
    path = settings.root / request.attachment_path
    data = base64.b64encode(path.read_bytes()).decode()
    mime = IMAGE_TYPES[path.suffix.lower()]
    return {"role": "user", "content": [
        {"type": "text", "text": text + "\nThe request has an attached image; read the order details from it."},
        {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{data}"}},
    ]}


def extract(request: IncomingRequest, catalog: Catalog, client: ChatClient, settings: Settings,
            store: Store) -> Extraction:
    system = settings.prompt_path.read_text(encoding="utf-8")
    messages: list[dict] = [{"role": "system", "content": system}, user_message(request, settings)]
    looked_up: set[str] = set()
    call_ids: list[str] = []
    invalid = 0

    for step in range(1, settings.max_steps + 1):
        try:
            result = client.complete(request.request_id, step, messages, TOOLS)
        except LLMError as exc:
            record = exc.args[1] if len(exc.args) > 1 else None
            if record:
                store.add_llm_call(record)
                call_ids.append(record["call_id"])
            raise ExtractionFailed(exc.code, str(exc.args[0])) from exc
        store.add_llm_call(result.record)
        call_ids.append(result.record["call_id"])
        message = result.message
        tool_calls = message.get("tool_calls") or []
        messages.append(message)
        if not tool_calls:
            invalid += 1
            if invalid > 1:
                raise ExtractionFailed("INVALID_MODEL_OUTPUT", "Model answered without calling a tool twice.")
            messages.append({"role": "user", "content": "Call search_catalog or submit_order_draft."})
            continue

        submission = None
        for tc in tool_calls:
            name, raw = tc["function"]["name"], tc["function"]["arguments"]
            if name == "search_catalog":
                try:
                    args = SearchCatalogArgs.model_validate_json(raw)
                except ValidationError as exc:
                    reply = {"error": f"invalid arguments: {exc.errors(include_url=False)}"}
                    store.add_tool_call(request.request_id, step, name, raw, reply)
                else:
                    results = catalog.search(args.query)
                    looked_up |= {r["sku"] for r in results}
                    reply = {"results": results} if results else {"results": [], "note": "no catalog product matches"}
                    store.add_tool_call(request.request_id, step, name, args.model_dump(), reply)
            elif name == "submit_order_draft":
                try:
                    submission = OrderDraftSubmission.model_validate_json(raw)
                    reply = {"status": "received"}
                except ValidationError as exc:
                    invalid += 1
                    reply = {"error": "invalid arguments; call submit_order_draft again with valid JSON",
                             "details": [e["msg"] for e in exc.errors(include_url=False)][:5]}
                    if invalid > 1:
                        store.add_tool_call(request.request_id, step, name, raw, reply)
                        raise ExtractionFailed("INVALID_MODEL_OUTPUT",
                                               f"Model returned invalid submit_order_draft arguments: "
                                               f"{reply['details']}") from exc
                store.add_tool_call(request.request_id, step, name,
                                    json.loads(raw) if submission else raw, reply)
            else:
                reply = {"error": f"unknown tool {name}"}
                store.add_tool_call(request.request_id, step, name, raw, reply)
            messages.append({"role": "tool", "tool_call_id": tc["id"], "content": json.dumps(reply)})
        if submission is not None:
            return Extraction(submission, looked_up, call_ids)

    raise ExtractionFailed("STEP_LIMIT", f"No valid draft after {settings.max_steps} model calls.")
