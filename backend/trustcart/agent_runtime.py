from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx
from google import genai
from google.genai import errors as genai_errors
from google.genai import types
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select

from .config import get_settings
from .crypto import create_proof, load_private_key, sha256_hex
from .db import SessionLocal
from .demo_faults import consume_demo_fault
from .enums import RunStatus
from .errors import TrustCartError
from .models import AgentRun, AgentToolEvent, DelegationGrant
from .schemas import QuoteItemInput

settings = get_settings()


@dataclass(slots=True)
class CommerceRunContext:
    run_id: uuid.UUID
    user_id: uuid.UUID
    agent_id: uuid.UUID
    grant_id: uuid.UUID
    grant_digest: str
    auto_execute: bool
    payment_mode: str
    active_quote: dict[str, Any] | None = None
    tool_calls: int = 0
    started_at: float = field(default_factory=time.monotonic)
    transport: httpx.AsyncBaseTransport | None = None
    signing_key_pem: str | None = None
    persist_events: bool = True
    checkout_created: bool = False
    approval_requested: bool = False

    def assert_budget(self) -> None:
        if self.tool_calls >= 10:
            raise TrustCartError(
                "TOOL_LIMIT_EXCEEDED", "The run reached its ten-tool safety limit", 409
            )
        if time.monotonic() - self.started_at >= 20:
            raise TrustCartError(
                "RUN_TIMEOUT", "The run reached its 20-second wall-clock limit", 408
            )
        if self.persist_events:
            with SessionLocal() as session:
                status = session.scalar(select(AgentRun.status).where(AgentRun.id == self.run_id))
            if status == RunStatus.CANCELLED:
                raise TrustCartError("RUN_CANCELLED", "The buyer cancelled this agent run", 409)


class ToolArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SearchCatalogArguments(ToolArguments):
    query: str = Field(min_length=1, max_length=200)
    category: str | None = None
    maximum_unit_price_paise: int | None = Field(default=None, ge=0)
    result_limit: int = Field(default=10, ge=1, le=30)


class EmptyArguments(ToolArguments):
    pass


class DeliveryArguments(ToolArguments):
    postcode: str = Field(pattern=r"^[1-9][0-9]{5}$")


class QuoteCartArguments(ToolArguments):
    items: list[QuoteItemInput] = Field(min_length=1, max_length=30)
    delivery_option_id: str = Field(min_length=1, max_length=64)


ToolHandler = Callable[[CommerceRunContext, ToolArguments], Awaitable[Any]]


@dataclass(frozen=True, slots=True)
class CommerceTool:
    name: str
    description: str
    arguments: type[ToolArguments]
    handler: ToolHandler

    @property
    def params_json_schema(self) -> dict[str, Any]:
        schema = self.arguments.model_json_schema()
        schema.pop("title", None)
        return schema

    def declaration(self) -> types.FunctionDeclaration:
        return types.FunctionDeclaration(
            name=self.name,
            description=self.description,
            parameters_json_schema=self.params_json_schema,
        )


@dataclass(frozen=True, slots=True)
class GeminiRunResult:
    final_output: str
    turns: int
    input_tokens: int
    output_tokens: int


def _record_tool(
    ctx: CommerceRunContext,
    name: str,
    inputs: Any,
    output: Any,
    summary: str,
    *,
    status: str = "SUCCEEDED",
) -> None:
    if not ctx.persist_events:
        return
    with SessionLocal.begin() as session:
        sequence = (
            session.scalar(
                select(func.coalesce(func.max(AgentToolEvent.sequence), 0)).where(
                    AgentToolEvent.run_id == ctx.run_id
                )
            )
            + 1
        )
        session.add(
            AgentToolEvent(
                run_id=ctx.run_id,
                sequence=sequence,
                tool_name=name,
                status=status,
                input_digest=sha256_hex(json.dumps(inputs, sort_keys=True, default=str)),
                output_digest=sha256_hex(json.dumps(output, sort_keys=True, default=str)),
                summary=summary,
                finished_at=datetime.now(UTC),
            )
        )
        run = session.get(AgentRun, ctx.run_id)
        if run:
            run.tool_call_count = ctx.tool_calls


async def merchant_request(
    ctx: CommerceRunContext,
    method: str,
    path: str,
    *,
    query: list[tuple[str, str]] | None = None,
    payload: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
) -> Any:
    configured_key = ctx.signing_key_pem or (
        settings.agent_private_key_pem.get_secret_value()
        if settings.agent_private_key_pem
        else None
    )
    if not configured_key:
        raise TrustCartError("AGENT_KEY_NOT_CONFIGURED", "Agent signing key is missing", 503)
    body = (
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        if payload is not None
        else b""
    )
    query = query or []
    proof = create_proof(
        load_private_key(configured_key),
        method=method,
        path=path,
        query_items=query,
        body=body,
        timestamp=int(time.time()),
        grant_id=str(ctx.grant_id),
        grant_digest=ctx.grant_digest,
    )
    headers = {
        "X-Agent-Id": str(ctx.agent_id),
        "X-Grant-Id": str(ctx.grant_id),
        "X-Agent-Timestamp": str(proof.timestamp),
        "X-Agent-Nonce": proof.nonce,
        "X-Body-SHA256": proof.body_sha256,
        "X-Agent-Signature": proof.signature,
        "Content-Type": "application/json",
    }
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    async with httpx.AsyncClient(
        base_url=settings.merchant_api_url,
        timeout=5,
        transport=ctx.transport,
    ) as client:
        response = await client.request(method, path, params=query, content=body, headers=headers)
    data = response.json()
    if response.is_error:
        raise TrustCartError(
            data.get("code", "MERCHANT_API_ERROR"),
            data.get("message", "Merchant API rejected the request"),
            response.status_code,
            data.get("details"),
        )
    return data


async def replay_nonce_fixture(ctx: CommerceRunContext) -> dict[str, Any]:
    """Send one valid proof twice to demonstrate durable replay rejection."""
    configured_key = ctx.signing_key_pem or (
        settings.agent_private_key_pem.get_secret_value()
        if settings.agent_private_key_pem
        else None
    )
    if not configured_key:
        raise TrustCartError("AGENT_KEY_NOT_CONFIGURED", "Agent signing key is missing", 503)
    path = "/v1/catalog/search"
    query = [("query", "milk"), ("limit", "1")]
    body = b""
    proof = create_proof(
        load_private_key(configured_key),
        method="GET",
        path=path,
        query_items=query,
        body=body,
        timestamp=int(time.time()),
        grant_id=str(ctx.grant_id),
        grant_digest=ctx.grant_digest,
    )
    headers = {
        "X-Agent-Id": str(ctx.agent_id),
        "X-Grant-Id": str(ctx.grant_id),
        "X-Agent-Timestamp": str(proof.timestamp),
        "X-Agent-Nonce": proof.nonce,
        "X-Body-SHA256": proof.body_sha256,
        "X-Agent-Signature": proof.signature,
    }
    async with httpx.AsyncClient(
        base_url=settings.merchant_api_url,
        timeout=5,
        transport=ctx.transport,
    ) as client:
        first = await client.get(path, params=query, headers=headers)
        second = await client.get(path, params=query, headers=headers)
    try:
        second_data = second.json()
    except ValueError:
        second_data = {}
    second_code = second_data.get("code")
    return {
        "first_status": first.status_code,
        "second_status": second.status_code,
        "second_code": second_code,
        "proof_replayed": first.is_success
        and second.status_code == 409
        and second_code == "PROOF_REPLAYED",
    }


async def create_signed_grant_request(agent_id: uuid.UUID, payload: dict[str, Any]) -> Any:
    """Submit a TC-AGENT-V1 request before any delegation grant exists."""
    if not settings.agent_private_key_pem:
        raise TrustCartError("AGENT_KEY_NOT_CONFIGURED", "Agent signing key is missing", 503)
    path = "/v1/grant-requests"
    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    proof = create_proof(
        load_private_key(settings.agent_private_key_pem.get_secret_value()),
        method="POST",
        path=path,
        query_items=[],
        body=body,
        timestamp=int(time.time()),
    )
    headers = {
        "X-Agent-Id": str(agent_id),
        "X-Agent-Timestamp": str(proof.timestamp),
        "X-Agent-Nonce": proof.nonce,
        "X-Body-SHA256": proof.body_sha256,
        "X-Agent-Signature": proof.signature,
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(base_url=settings.merchant_api_url, timeout=5) as client:
        response = await client.post(path, content=body, headers=headers)
    data = response.json()
    if response.is_error:
        raise TrustCartError(
            data.get("code", "GRANT_REQUEST_FAILED"),
            data.get("message", "Merchant rejected the grant request"),
            response.status_code,
            data.get("details"),
        )
    return data


async def search_catalog(
    ctx: CommerceRunContext, arguments: SearchCatalogArguments
) -> list[dict[str, Any]]:
    """Search factual products. Prices are exact, tax-inclusive paise and never model-generated."""
    params = [("query", arguments.query), ("limit", str(arguments.result_limit))]
    if arguments.category:
        params.append(("category", arguments.category))
    if arguments.maximum_unit_price_paise is not None:
        params.append(
            ("max_unit_price_paise", str(arguments.maximum_unit_price_paise))
        )
    result = await merchant_request(ctx, "GET", "/v1/catalog/search", query=params)
    _record_tool(
        ctx, "search_catalog", params, result, f"Found {len(result)} factual catalog candidates."
    )
    return result


async def get_usual_basket(
    ctx: CommerceRunContext, _: EmptyArguments
) -> dict[str, Any]:
    """Return the authenticated buyer's seeded usual basket; buyer identity is injected by context."""
    result = await merchant_request(ctx, "GET", "/v1/users/me/usual-basket")
    _record_tool(
        ctx,
        "get_usual_basket",
        {},
        result,
        "Loaded factual purchase history for the authenticated buyer.",
    )
    return result


async def get_delivery_options(
    ctx: CommerceRunContext, arguments: DeliveryArguments
) -> list[dict[str, Any]]:
    """Return exact delivery slots, cutoff timestamps and fees for an Indian postcode."""
    ctx.active_quote = None
    result = await merchant_request(
        ctx,
        "POST",
        "/v1/delivery-options",
        payload={"postcode": arguments.postcode},
    )
    _record_tool(
        ctx,
        "get_delivery_options",
        {"postcode": arguments.postcode},
        result,
        "Loaded deterministic delivery fees and cutoffs.",
    )
    return result


async def quote_cart(
    ctx: CommerceRunContext, arguments: QuoteCartArguments
) -> dict[str, Any]:
    """Create a canonical immutable quote from SKU/quantity items and one delivery option."""
    ctx.active_quote = None
    serialized_items = [item.model_dump() for item in arguments.items]
    result = await merchant_request(
        ctx,
        "POST",
        "/v1/quotes",
        payload={
            "items": serialized_items,
            "delivery_option_id": arguments.delivery_option_id,
        },
    )
    ctx.active_quote = result
    if ctx.persist_events:
        with SessionLocal.begin() as session:
            run = session.get(AgentRun, ctx.run_id)
            if run:
                run.active_quote_id = uuid.UUID(result["id"])
    _record_tool(
        ctx,
        "quote_cart",
        {
            "items": serialized_items,
            "delivery_option_id": arguments.delivery_option_id,
        },
        result,
        f"Canonical quote totals ₹{result['total_paise'] / 100:.2f}.",
    )
    return result


def _has_live_quote(ctx: CommerceRunContext) -> bool:
    if not ctx.active_quote or not ctx.active_quote.get("expires_at"):
        return False
    try:
        expires = datetime.fromisoformat(str(ctx.active_quote["expires_at"]).replace("Z", "+00:00"))
    except ValueError:
        return False
    return expires > datetime.now(UTC)


async def place_order(
    ctx: CommerceRunContext, _: EmptyArguments | None = None
) -> dict[str, Any]:
    """Place the active trusted quote. Takes no quote ID, price, grant, buyer, or idempotency input."""
    if not _has_live_quote(ctx):
        raise TrustCartError("QUOTE_REQUIRED", "A valid quote must be created before checkout", 409)
    key = sha256_hex(f"{ctx.run_id}:checkout.create.v1")[:48]
    result = await merchant_request(
        ctx,
        "POST",
        "/v1/checkouts",
        payload={"quote_id": ctx.active_quote["id"], "payment_mode": ctx.payment_mode},
        idempotency_key=key,
    )
    ctx.checkout_created = True
    cancelled = False
    if ctx.persist_events:
        with SessionLocal.begin() as session:
            run = session.scalar(
                select(AgentRun).where(AgentRun.id == ctx.run_id).with_for_update()
            )
            if run:
                run.checkout_id = uuid.UUID(result["id"])
                cancelled = run.status == RunStatus.CANCELLED
    if cancelled:
        await merchant_request(ctx, "POST", f"/v1/checkouts/{result['id']}/cancel")
        raise TrustCartError(
            "RUN_CANCELLED",
            "The buyer cancelled while checkout was being reserved; the reservation was released",
            409,
        )
    _record_tool(
        ctx,
        "place_order",
        {},
        result,
        "Checkout passed deterministic authorization and entered the cancellation window.",
    )
    return result


async def request_purchase_approval(
    ctx: CommerceRunContext, _: EmptyArguments | None = None
) -> dict[str, Any]:
    """Return the active proposal when the grant requires explicit purchase approval."""
    if not _has_live_quote(ctx):
        raise TrustCartError("QUOTE_REQUIRED", "A quote is required before approval", 409)
    result = {
        "outcome": "proposal_ready",
        "quote": ctx.active_quote,
        "requires_user_approval": True,
    }
    ctx.approval_requested = True
    _record_tool(
        ctx,
        "request_purchase_approval",
        {},
        result,
        "Grant requires explicit user approval; no Checkout was created.",
    )
    return result


INSTRUCTIONS = """You are AgentAuth's single commerce discovery agent.
Interpret the buyer's goal and use tools for facts. Never invent a price, total, SKU, user, grant,
quote ID, or checkout state. You MUST obtain a successful canonical quote before purchase. Browse-only
or comparative requests must never purchase. Ask a concise clarification when constraints are ambiguous.
When auto-execution is available and the user explicitly asked to buy, call place_order. The application,
not you, controls whether that tool is visible. Call at most one tool in each turn. Keep the final answer
short and state whether the result is a proposal, scheduled checkout, payment pending, paid, or
simulated; Order creation is never payment.
"""


DISCOVERY_TOOLS = (
    CommerceTool(
        "search_catalog",
        "Search exact merchant catalog facts. Prices are tax-inclusive paise.",
        SearchCatalogArguments,
        search_catalog,
    ),
    CommerceTool(
        "get_usual_basket",
        "Load the authenticated buyer's factual usual basket. Takes no buyer identifier.",
        EmptyArguments,
        get_usual_basket,
    ),
    CommerceTool(
        "get_delivery_options",
        "Return deterministic delivery slots, cutoffs, and fees for one Indian postcode.",
        DeliveryArguments,
        get_delivery_options,
    ),
    CommerceTool(
        "quote_cart",
        "Create the canonical merchant quote from SKU quantities and a delivery option.",
        QuoteCartArguments,
        quote_cart,
    ),
)
PLACE_ORDER_TOOL = CommerceTool(
    "place_order",
    "Place only the active trusted quote. Takes no model-controlled fields.",
    EmptyArguments,
    place_order,
)
APPROVAL_TOOL = CommerceTool(
    "request_purchase_approval",
    "Return the active quote for explicit approval without creating a Checkout.",
    EmptyArguments,
    request_purchase_approval,
)
ALL_TOOLS = (*DISCOVERY_TOOLS, PLACE_ORDER_TOOL, APPROVAL_TOOL)


def available_tools(ctx: CommerceRunContext) -> tuple[CommerceTool, ...]:
    """Expose action tools only when trusted runtime state satisfies their gates."""
    if ctx.checkout_created or ctx.approval_requested:
        return ()
    if not _has_live_quote(ctx):
        return DISCOVERY_TOOLS
    action = PLACE_ORDER_TOOL if ctx.auto_execute else APPROVAL_TOOL
    return (*DISCOVERY_TOOLS, action)


def _tool_error(exc: TrustCartError) -> dict[str, Any]:
    return {
        "ok": False,
        "error": {"code": exc.code, "message": exc.message, "details": exc.details},
    }


async def execute_tool(
    ctx: CommerceRunContext, name: str, raw_arguments: dict[str, Any]
) -> dict[str, Any]:
    ctx.assert_budget()
    ctx.tool_calls += 1
    allowed = {tool.name: tool for tool in available_tools(ctx)}
    tool = allowed.get(name)
    if tool is None:
        exc = TrustCartError(
            "TOOL_NOT_AVAILABLE",
            "That tool is not available in the current trusted run state",
            409,
        )
        _record_tool(
            ctx, name, raw_arguments, _tool_error(exc), exc.message, status="REJECTED"
        )
        return _tool_error(exc)
    try:
        arguments = tool.arguments.model_validate(raw_arguments)
    except Exception as validation_error:
        exc = TrustCartError(
            "INVALID_TOOL_ARGUMENTS",
            f"Arguments for {name} failed strict validation",
            422,
            {"validation": str(validation_error)[:500]},
        )
        _record_tool(
            ctx, name, raw_arguments, _tool_error(exc), exc.message, status="REJECTED"
        )
        return _tool_error(exc)
    try:
        result = await asyncio.wait_for(tool.handler(ctx, arguments), timeout=5)
        return {"ok": True, "result": result}
    except TimeoutError:
        exc = TrustCartError("TOOL_TIMEOUT", f"{name} exceeded its five-second limit", 408)
        _record_tool(ctx, name, raw_arguments, _tool_error(exc), exc.message, status="FAILED")
        return _tool_error(exc)
    except TrustCartError as exc:
        _record_tool(ctx, name, raw_arguments, _tool_error(exc), exc.message, status="FAILED")
        if exc.code in {"RUN_CANCELLED", "RUN_TIMEOUT", "TOOL_LIMIT_EXCEEDED"}:
            raise
        return _tool_error(exc)
    except Exception:
        exc = TrustCartError(
            "TOOL_EXECUTION_FAILED",
            f"{name} failed without changing trusted commerce state",
            502,
        )
        _record_tool(ctx, name, raw_arguments, _tool_error(exc), exc.message, status="FAILED")
        return _tool_error(exc)


def _thinking_level(value: str) -> types.ThinkingLevel:
    levels = {
        "minimal": types.ThinkingLevel.MINIMAL,
        "low": types.ThinkingLevel.LOW,
        "medium": types.ThinkingLevel.MEDIUM,
        "high": types.ThinkingLevel.HIGH,
    }
    try:
        return levels[value.lower()]
    except KeyError as exc:
        raise TrustCartError(
            "INVALID_THINKING_LEVEL",
            "Gemini thinking level must be minimal, low, medium, or high",
            500,
        ) from exc


class GeminiCommerceAgent:
    """One bounded Gemini agent loop; application code owns tools and state."""

    def __init__(
        self,
        thinking_level: str | None = None,
        *,
        client_factory: Callable[..., Any] = genai.Client,
    ) -> None:
        self.model = settings.model_name
        self.thinking_level = thinking_level or settings.model_thinking_level
        self.tools = ALL_TOOLS
        self._client_factory = client_factory

    def visible_tools(self, ctx: CommerceRunContext) -> tuple[CommerceTool, ...]:
        return available_tools(ctx)

    async def run(
        self,
        message: str,
        ctx: CommerceRunContext,
        *,
        api_key: str,
    ) -> GeminiRunResult:
        client = self._client_factory(
            api_key=api_key,
            http_options=types.HttpOptions(
                api_version="v1",
                headers={"x-goog-api-client": "agentauth-buildathon/0.1.0"},
            ),
        )
        contents: list[types.Content] = [
            types.Content(role="user", parts=[types.Part.from_text(text=message)])
        ]
        input_tokens = 0
        output_tokens = 0
        try:
            for turn in range(1, 9):
                ctx.assert_budget()
                visible = self.visible_tools(ctx)
                sdk_tools = (
                    [types.Tool(function_declarations=[tool.declaration() for tool in visible])]
                    if visible
                    else None
                )
                try:
                    response = await client.aio.models.generate_content(
                        model=self.model,
                        contents=contents,
                        config=types.GenerateContentConfig(
                            system_instruction=INSTRUCTIONS,
                            temperature=0.2,
                            max_output_tokens=600,
                            tools=sdk_tools,
                            tool_config=(
                                types.ToolConfig(
                                    function_calling_config=types.FunctionCallingConfig(
                                        mode=types.FunctionCallingConfigMode.AUTO
                                    )
                                )
                                if sdk_tools
                                else None
                            ),
                            automatic_function_calling=types.AutomaticFunctionCallingConfig(
                                disable=True
                            ),
                            thinking_config=types.ThinkingConfig(
                                thinking_level=_thinking_level(self.thinking_level)
                            ),
                        ),
                    )
                except genai_errors.ClientError as exc:
                    status = int(getattr(exc, "code", 400) or 400)
                    if status in {401, 403}:
                        code, message = (
                            "MODEL_AUTH_FAILED",
                            "Gemini rejected the configured API key or model access",
                        )
                    elif status == 429:
                        code, message = (
                            "MODEL_RATE_LIMITED",
                            "Gemini free-tier quota is temporarily unavailable",
                        )
                    else:
                        code, message = (
                            "MODEL_REQUEST_REJECTED",
                            "Gemini rejected the bounded agent request",
                        )
                    raise TrustCartError(code, message, 503) from exc
                except genai_errors.ServerError as exc:
                    raise TrustCartError(
                        "MODEL_PROVIDER_UNAVAILABLE",
                        "Gemini is temporarily unavailable; the run can be retried safely",
                        503,
                    ) from exc
                usage = response.usage_metadata
                if usage:
                    prompt = usage.prompt_token_count or 0
                    input_tokens += prompt
                    output_tokens += max(0, (usage.total_token_count or 0) - prompt)
                if not response.candidates or response.candidates[0].content is None:
                    raise TrustCartError(
                        "MODEL_EMPTY_RESPONSE", "Gemini returned no candidate content", 502
                    )
                model_content = response.candidates[0].content
                contents.append(model_content)
                calls = [
                    part.function_call
                    for part in (model_content.parts or [])
                    if part.function_call is not None
                ]
                if len(calls) > 1:
                    parts = [
                        types.Part(
                            function_response=types.FunctionResponse(
                                id=call.id,
                                name=call.name,
                                response={
                                    "ok": False,
                                    "error": {
                                        "code": "PARALLEL_TOOL_CALLS_REJECTED",
                                        "message": "Call exactly one commerce tool per turn",
                                    },
                                },
                            )
                        )
                        for call in calls
                    ]
                    contents.append(types.Content(role="user", parts=parts))
                    continue
                if calls:
                    call = calls[0]
                    name = str(call.name or "")
                    result = await execute_tool(ctx, name, dict(call.args or {}))
                    contents.append(
                        types.Content(
                            role="user",
                            parts=[
                                types.Part(
                                    function_response=types.FunctionResponse(
                                        id=call.id,
                                        name=name,
                                        response=result,
                                    )
                                )
                            ],
                        )
                    )
                    continue
                final = "".join(
                    part.text or "" for part in (model_content.parts or []) if part.text
                ).strip()
                if not final:
                    raise TrustCartError(
                        "MODEL_EMPTY_RESPONSE", "Gemini returned neither text nor a tool call", 502
                    )
                return GeminiRunResult(final, turn, input_tokens, output_tokens)
            raise TrustCartError("MODEL_TURN_LIMIT", "Gemini reached the eight-turn limit", 409)
        finally:
            await client.aio.aclose()


def build_agent(thinking_level: str | None = None) -> GeminiCommerceAgent:
    return GeminiCommerceAgent(thinking_level)


async def run_commerce_agent(run_id: uuid.UUID) -> None:
    with SessionLocal.begin() as session:
        run = session.scalar(select(AgentRun).where(AgentRun.id == run_id).with_for_update())
        if run is None or run.status != RunStatus.QUEUED:
            return
        grant = session.get(DelegationGrant, run.grant_id)
        now = datetime.now(UTC)
        if (
            grant is None
            or grant.user_id != run.user_id
            or grant.status != "ACTIVE"
            or not (grant.valid_from <= now < grant.expires_at)
        ):
            run.status, run.error_code = RunStatus.FAILED, "GRANT_NOT_ACTIVE"
            return
        run.status = RunStatus.RUNNING
        context = CommerceRunContext(
            run.id,
            run.user_id,
            run.agent_id,
            run.grant_id,
            grant.immutable_digest,
            grant.auto_execute,
            run.payment_mode,
        )
        message = run.user_message
    try:
        if consume_demo_fault("FORCE_MODEL_TIMEOUT"):
            raise TrustCartError(
                "MODEL_TIMEOUT_INJECTED",
                "The developer fixture forced a typed model-timeout outcome",
                408,
            )
        if not settings.gemini_api_key:
            raise TrustCartError(
                "GEMINI_NOT_CONFIGURED",
                "Set TRUSTCART_GEMINI_API_KEY to run the genuine tool-using agent",
                503,
            )
        result = await asyncio.wait_for(
            build_agent().run(
                message,
                context,
                api_key=settings.gemini_api_key.get_secret_value(),
            ),
            timeout=20,
        )
        final = result.final_output
        with SessionLocal.begin() as session:
            run = session.get(AgentRun, run_id)
            if run and run.status == RunStatus.RUNNING:
                run.status = (
                    RunStatus.CHECKOUT_SCHEDULED if run.checkout_id else RunStatus.PROPOSAL_READY
                )
                run.final_response = final
                run.tool_call_count = context.tool_calls
                run.turn_count = result.turns
                run.completed_at = datetime.now(UTC)
    except Exception as exc:
        with SessionLocal.begin() as session:
            run = session.get(AgentRun, run_id)
            if run and run.status == RunStatus.RUNNING:
                run.status = RunStatus.FAILED
                run.error_code = (
                    exc.code
                    if isinstance(exc, TrustCartError)
                    else "MODEL_TIMEOUT"
                    if isinstance(exc, TimeoutError)
                    else "AGENT_RUN_FAILED"
                )
                run.error_detail = str(exc)[:1_000]
                run.completed_at = datetime.now(UTC)
