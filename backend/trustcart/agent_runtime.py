from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx
from agents import (
    Agent,
    ModelSettings,
    RunContextWrapper,
    Runner,
    function_tool,
    set_default_openai_key,
)
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


def _record_tool(
    ctx: CommerceRunContext, name: str, inputs: Any, output: Any, summary: str
) -> None:
    ctx.tool_calls += 1
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
                status="SUCCEEDED",
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


@function_tool(timeout=5)
async def search_catalog(
    wrapper: RunContextWrapper[CommerceRunContext],
    query: str,
    category: str | None = None,
    maximum_unit_price_paise: int | None = None,
    result_limit: int = 10,
) -> list[dict[str, Any]]:
    """Search factual products. Prices are exact, tax-inclusive paise and never model-generated."""
    ctx = wrapper.context
    ctx.assert_budget()
    params = [("query", query), ("limit", str(min(result_limit, 30)))]
    if category:
        params.append(("category", category))
    if maximum_unit_price_paise is not None:
        params.append(("max_unit_price_paise", str(maximum_unit_price_paise)))
    result = await merchant_request(ctx, "GET", "/v1/catalog/search", query=params)
    _record_tool(
        ctx, "search_catalog", params, result, f"Found {len(result)} factual catalog candidates."
    )
    return result


@function_tool(timeout=5)
async def get_usual_basket(wrapper: RunContextWrapper[CommerceRunContext]) -> dict[str, Any]:
    """Return the authenticated buyer's seeded usual basket; buyer identity is injected by context."""
    ctx = wrapper.context
    ctx.assert_budget()
    result = await merchant_request(ctx, "GET", "/v1/users/me/usual-basket")
    _record_tool(
        ctx,
        "get_usual_basket",
        {},
        result,
        "Loaded factual purchase history for the authenticated buyer.",
    )
    return result


@function_tool(timeout=5)
async def get_delivery_options(
    wrapper: RunContextWrapper[CommerceRunContext], postcode: str
) -> list[dict[str, Any]]:
    """Return exact delivery slots, cutoff timestamps and fees for an Indian postcode."""
    ctx = wrapper.context
    ctx.assert_budget()
    ctx.active_quote = None
    result = await merchant_request(
        ctx, "POST", "/v1/delivery-options", payload={"postcode": postcode}
    )
    _record_tool(
        ctx,
        "get_delivery_options",
        {"postcode": postcode},
        result,
        "Loaded deterministic delivery fees and cutoffs.",
    )
    return result


@function_tool(timeout=5)
async def quote_cart(
    wrapper: RunContextWrapper[CommerceRunContext],
    items: list[QuoteItemInput],
    delivery_option_id: str,
) -> dict[str, Any]:
    """Create a canonical immutable quote from SKU/quantity items and one delivery option."""
    ctx = wrapper.context
    ctx.assert_budget()
    serialized_items = [item.model_dump() for item in items]
    result = await merchant_request(
        ctx,
        "POST",
        "/v1/quotes",
        payload={"items": serialized_items, "delivery_option_id": delivery_option_id},
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
        {"items": serialized_items, "delivery_option_id": delivery_option_id},
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


def _can_place(wrapper: RunContextWrapper[CommerceRunContext], _: Agent) -> bool:
    return _has_live_quote(wrapper.context) and wrapper.context.auto_execute


def _can_approve(wrapper: RunContextWrapper[CommerceRunContext], _: Agent) -> bool:
    return _has_live_quote(wrapper.context) and not wrapper.context.auto_execute


@function_tool(is_enabled=_can_place, timeout=5)
async def place_order(wrapper: RunContextWrapper[CommerceRunContext]) -> dict[str, Any]:
    """Place the active trusted quote. Takes no quote ID, price, grant, buyer, or idempotency input."""
    ctx = wrapper.context
    ctx.assert_budget()
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


@function_tool(is_enabled=_can_approve, timeout=5)
async def request_purchase_approval(
    wrapper: RunContextWrapper[CommerceRunContext],
) -> dict[str, Any]:
    """Return the active proposal when the grant requires explicit purchase approval."""
    ctx = wrapper.context
    ctx.assert_budget()
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
not you, controls whether that tool is visible. Keep the final answer short and state whether the result is
a proposal, scheduled checkout, payment pending, paid, or simulated; Order creation is never payment.
"""


def build_agent(reasoning_effort: str | None = None) -> Agent[CommerceRunContext]:
    return Agent[CommerceRunContext](
        name="AgentAuth Commerce Agent",
        instructions=INSTRUCTIONS,
        model=settings.model_name,
        model_settings=ModelSettings(
            parallel_tool_calls=False,
            reasoning={"effort": reasoning_effort or settings.model_reasoning_effort},
            timeout=20,
        ),
        tools=[
            search_catalog,
            get_usual_basket,
            get_delivery_options,
            quote_cart,
            place_order,
            request_purchase_approval,
        ],
    )


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
        if not settings.openai_api_key:
            raise TrustCartError(
                "OPENAI_NOT_CONFIGURED",
                "Set TRUSTCART_OPENAI_API_KEY to run the genuine tool-using agent",
                503,
            )
        set_default_openai_key(settings.openai_api_key.get_secret_value())
        result = await asyncio.wait_for(
            Runner.run(build_agent(), message, context=context, max_turns=8), timeout=20
        )
        final = str(result.final_output)
        with SessionLocal.begin() as session:
            run = session.get(AgentRun, run_id)
            if run and run.status == RunStatus.RUNNING:
                run.status = (
                    RunStatus.CHECKOUT_SCHEDULED if run.checkout_id else RunStatus.PROPOSAL_READY
                )
                run.final_response = final
                run.tool_call_count = context.tool_calls
                run.turn_count = min(8, context.tool_calls + 1)
                run.completed_at = datetime.now(UTC)
    except Exception as exc:
        with SessionLocal.begin() as session:
            run = session.get(AgentRun, run_id)
            if run and run.status == RunStatus.RUNNING:
                run.status = RunStatus.FAILED
                run.error_code = exc.code if isinstance(exc, TrustCartError) else "AGENT_RUN_FAILED"
                run.error_detail = str(exc)[:1_000]
                run.completed_at = datetime.now(UTC)
