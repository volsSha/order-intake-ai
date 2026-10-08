"""Extraction agent: the model may only search the catalog and submit one draft, within a request cap."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated

from pydantic import Field
from pydantic_ai import Agent, BinaryContent, RunContext, ToolOutput, UsageLimits, capture_run_messages
from pydantic_ai.capabilities import Hooks
from pydantic_ai.exceptions import UnexpectedModelBehavior, UsageLimitExceeded
from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, RetryPromptPart, ToolCallPart

from ..config import Settings
from ..domain.catalog import Catalog
from ..domain.inbox import IMAGE_TYPES, IncomingRequest
from ..storage import Store
from .replay import LLMError, ReplayModel
from .schemas import OrderDraftSubmission

SEARCH = "search_catalog"
SUBMIT = "submit_order_draft"


class ExtractionFailed(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass
class Extraction:
    submission: OrderDraftSubmission
    looked_up_skus: set[str]
    llm_call_ids: list[str] = field(default_factory=list)


@dataclass
class Deps:
    catalog: Catalog
    looked_up: set[str] = field(default_factory=set)


def image_part(root: Path, relative: str) -> BinaryContent:
    path = root / relative
    return BinaryContent(data=path.read_bytes(), media_type=IMAGE_TYPES[path.suffix.lower()])


def user_prompt(request: IncomingRequest, settings: Settings) -> list:
    text = (f"Order request {request.request_id}, order reference {request.order_ref}.\n"
            f"<request>\n{request.body or '(empty body)'}\n</request>")
    if not request.attachment_path:
        return [text]
    return [text + "\nThe request has an attached image; read the order details from it.",
            image_part(settings.root, request.attachment_path)]


def _repair_budget() -> Hooks:
    hooks = Hooks()

    # PydanticAI counts text, unknown-tool and bad-output retries separately; R7 allows one in total
    @hooks.on.before_model_request
    async def one_repair(ctx, request_context):
        repairs = sum(isinstance(p, RetryPromptPart) and p.tool_name != SEARCH
                      for m in request_context.messages if isinstance(m, ModelRequest) for p in m.parts)
        if repairs > 1:
            raise UnexpectedModelBehavior("Exceeded maximum output retries (1)")
        return request_context

    return hooks


def build_agent(model: ReplayModel, settings: Settings) -> Agent[Deps, OrderDraftSubmission]:
    agent = Agent(
        model,
        output_type=ToolOutput(OrderDraftSubmission, name=SUBMIT, strict=True, max_retries=1,
                               description="Submit the final interpretation of the request. Call exactly once, "
                                           "after searching."),
        instructions=settings.prompt_path.read_text(encoding="utf-8"),
        deps_type=Deps,
        retries={"tools": settings.max_steps, "output": 1},
        end_strategy="exhaustive",
        capabilities=[_repair_budget()],
    )

    @agent.tool(sequential=True, strict=True)
    def search_catalog(ctx: RunContext[Deps], query: Annotated[str, Field(min_length=1, max_length=120)]) -> dict:
        """Search the local product catalog by SKU or description words. Returns matching catalog entries
        (sku, name, unit_cents). This is the only source of product information."""
        results = ctx.deps.catalog.search(query)
        ctx.deps.looked_up |= {r["sku"] for r in results}
        return {"results": results} if results else {"results": [], "note": "no catalog product matches"}

    return agent


def _arguments(call: ToolCallPart):
    try:
        return call.args_as_dict(raise_if_invalid=True)
    except (ValueError, AssertionError):
        return call.args


def _reply(part) -> object:
    if isinstance(part, RetryPromptPart):
        if isinstance(part.content, str):
            return {"error": part.content}
        return {"error": "invalid arguments", "details": [e["msg"] for e in part.content][:5]}
    return part.content


def record_tool_calls(store: Store, request_id: str, messages: list[ModelMessage], call_ids: list[str],
                      failure: str | None = None) -> None:
    replies = {p.tool_call_id: p for m in messages if isinstance(m, ModelRequest) for p in m.parts
               if hasattr(p, "tool_call_id")}
    responses = [m for m in messages if isinstance(m, ModelResponse)]
    rows = []
    for step, (response, call_id) in enumerate(zip(responses, call_ids, strict=False), start=1):
        for call in response.tool_calls:
            reply = replies.get(call.tool_call_id)
            result = _reply(reply) if reply else ({"error": failure} if failure else None)
            rows.append((request_id, step, call.tool_name, _arguments(call), result, call_id))
    store.add_tool_calls(rows)


def extract(request: IncomingRequest, catalog: Catalog, model: ReplayModel, settings: Settings,
            store: Store) -> Extraction:
    deps = Deps(catalog)
    agent = build_agent(model, settings)
    failure = None
    with capture_run_messages() as messages:
        try:
            result = agent.run_sync(user_prompt(request, settings), deps=deps,
                                    usage_limits=UsageLimits(request_limit=settings.max_steps))
        except LLMError as exc:
            failure = str(exc)
            raise ExtractionFailed(exc.code, failure) from exc
        except UsageLimitExceeded as exc:
            failure = f"No valid draft after {settings.max_steps} model calls."
            raise ExtractionFailed("STEP_LIMIT", failure) from exc
        except UnexpectedModelBehavior as exc:
            failure = f"Model returned invalid output twice: {exc}"
            raise ExtractionFailed("INVALID_MODEL_OUTPUT", failure) from exc
        finally:
            record_tool_calls(store, request.request_id, messages, model.call_ids, failure)
    return Extraction(result.output, deps.looked_up, list(model.call_ids))
