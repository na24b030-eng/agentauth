from __future__ import annotations

import asyncio
import json
import logging
import uuid
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime, timedelta

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .agent_runtime import (
    CommerceRunContext,
    create_signed_grant_request,
    merchant_request,
    replay_nonce_fixture,
    run_commerce_agent,
)
from .auth import agent_user
from .config import get_settings
from .db import SessionLocal, get_session
from .enums import RunStatus
from .errors import AuthorizationError, TrustCartError
from .models import AgentRun, AgentToolEvent, DelegationGrant, RegisteredAgent, User
from .schemas import (
    AgentRunCreate,
    AgentRunOut,
    CheckoutOut,
    GrantRequestCreate,
    NonceReplayOut,
    NonceReplayRequest,
    QuoteOut,
)

settings = get_settings()
logger = logging.getLogger(__name__)


async def _agent_run_dispatcher() -> None:
    """Dispatch durable queued model runs; payment work remains owned by the worker service."""
    active: set[asyncio.Task[None]] = set()
    while True:
        try:
            finished = {task for task in active if task.done()}
            for task in finished:
                with suppress(Exception):
                    task.result()
            active -= finished
            capacity = max(0, 4 - len(active))
            if capacity:
                with SessionLocal() as session:
                    run_ids = session.scalars(
                        select(AgentRun.id)
                        .where(AgentRun.status == RunStatus.QUEUED)
                        .order_by(AgentRun.created_at)
                        .limit(capacity)
                    ).all()
                for run_id in run_ids:
                    active.add(asyncio.create_task(run_commerce_agent(run_id)))
            await asyncio.sleep(0.25)
        except asyncio.CancelledError:
            for task in active:
                task.cancel()
            await asyncio.gather(*active, return_exceptions=True)
            raise
        except Exception:
            logger.exception("Agent run dispatcher iteration failed")
            await asyncio.sleep(1)


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Runs are bounded to 20 seconds. Anything older is an interrupted process, not live work.
    with SessionLocal.begin() as session:
        stale_before = datetime.now(UTC) - timedelta(minutes=1)
        stale = session.scalars(
            select(AgentRun).where(
                AgentRun.status == RunStatus.RUNNING, AgentRun.updated_at < stale_before
            )
        ).all()
        for run in stale:
            run.status = RunStatus.QUEUED
            run.error_code = "RECOVERED_INTERRUPTED_RUN"
    dispatcher = asyncio.create_task(_agent_run_dispatcher())
    try:
        yield
    finally:
        dispatcher.cancel()
        with suppress(asyncio.CancelledError):
            await dispatcher


app = FastAPI(title="AgentAuth Buyer Agent API", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        origin.strip() for origin in settings.frontend_origin.split(",") if origin.strip()
    ],
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=False,
)


@app.exception_handler(TrustCartError)
async def trustcart_error_handler(_: Request, exc: TrustCartError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"code": exc.code, "message": exc.message, "details": exc.details},
    )


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "service": "agent-api",
        "model_provider": "google-gemini",
        "model": settings.model_name,
        "gemini_configured": bool(settings.gemini_api_key),
    }


@app.post("/v1/developer/replay-nonce", response_model=NonceReplayOut)
async def replay_nonce(
    payload: NonceReplayRequest,
    user: User = Depends(agent_user),
    session: Session = Depends(get_session),
) -> NonceReplayOut:
    if settings.environment == "production":
        raise AuthorizationError(
            "DEVELOPER_FIXTURES_DISABLED",
            "Failure fixtures are disabled in production environments",
            403,
        )
    grant = session.get(DelegationGrant, payload.grant_id)
    now = datetime.now(UTC)
    if (
        grant is None
        or grant.user_id != user.id
        or grant.status != "ACTIVE"
        or not (grant.valid_from <= now < grant.expires_at)
    ):
        raise AuthorizationError("GRANT_NOT_ACTIVE", "An active grant is required", 403)
    context = CommerceRunContext(
        uuid.uuid4(),
        user.id,
        grant.agent_id,
        grant.id,
        grant.immutable_digest,
        grant.auto_execute,
        "DELEGATED_DEBIT_SIMULATOR",
        persist_events=False,
    )
    session.rollback()
    return NonceReplayOut.model_validate(await replay_nonce_fixture(context))


@app.post("/v1/grant-requests")
async def request_grant(
    payload: GrantRequestCreate,
    user: User = Depends(agent_user),
    session: Session = Depends(get_session),
) -> dict:
    if payload.user_id != user.id:
        raise AuthorizationError("WRONG_USER", "Grant request user does not match the session", 403)
    agent = session.scalar(
        select(RegisteredAgent)
        .where(RegisteredAgent.status == "ACTIVE")
        .order_by(RegisteredAgent.created_at.desc())
        .limit(1)
    )
    if agent is None:
        raise TrustCartError("AGENT_NOT_REGISTERED", "No active agent registration exists", 409)
    return await create_signed_grant_request(agent.id, payload.model_dump(mode="json"))


@app.post("/v1/agent-runs", response_model=AgentRunOut)
async def start_run(
    payload: AgentRunCreate,
    user: User = Depends(agent_user),
    session: Session = Depends(get_session),
) -> AgentRunOut:
    grant = session.get(DelegationGrant, payload.grant_id)
    now = datetime.now(UTC)
    if (
        grant is None
        or grant.user_id != user.id
        or grant.status != "ACTIVE"
        or not (grant.valid_from <= now < grant.expires_at)
    ):
        raise AuthorizationError(
            "GRANT_NOT_ACTIVE", "The selected grant is not active for this user", 403
        )
    run = AgentRun(
        user_id=user.id,
        agent_id=grant.agent_id,
        grant_id=grant.id,
        user_message=payload.message,
        payment_mode=payload.payment_mode,
        model_name=settings.model_name,
        thinking_level=settings.model_thinking_level,
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    return AgentRunOut.model_validate(run)


@app.get("/v1/agent-runs/{run_id}", response_model=AgentRunOut)
def get_run(
    run_id: uuid.UUID, user: User = Depends(agent_user), session: Session = Depends(get_session)
) -> AgentRunOut:
    run = session.get(AgentRun, run_id)
    if run is None or run.user_id != user.id:
        raise TrustCartError("RUN_NOT_FOUND", "Agent run was not found", 404)
    return AgentRunOut.model_validate(run)


@app.get("/v1/agent-runs/{run_id}/events")
async def run_events(run_id: uuid.UUID, user: User = Depends(agent_user)) -> StreamingResponse:
    async def events():
        seen = 0
        for _ in range(120):
            with SessionLocal() as session:
                run = session.get(AgentRun, run_id)
                if run is None or run.user_id != user.id:
                    yield 'event: error\ndata: {"code":"RUN_NOT_FOUND"}\n\n'
                    return
                rows = session.scalars(
                    select(AgentToolEvent)
                    .where(AgentToolEvent.run_id == run_id, AgentToolEvent.sequence > seen)
                    .order_by(AgentToolEvent.sequence)
                ).all()
                for row in rows:
                    seen = row.sequence
                    yield f"event: tool\ndata: {json.dumps({'sequence': row.sequence, 'tool': row.tool_name, 'status': row.status, 'summary': row.summary})}\n\n"
                yield f"event: state\ndata: {json.dumps({'status': run.status, 'quote_id': str(run.active_quote_id) if run.active_quote_id else None, 'checkout_id': str(run.checkout_id) if run.checkout_id else None, 'final_response': run.final_response, 'error_code': run.error_code})}\n\n"
                if run.status not in {RunStatus.QUEUED, RunStatus.RUNNING}:
                    return
            await asyncio.sleep(0.5)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _run_context(run: AgentRun, session: Session) -> CommerceRunContext:
    grant = session.get(DelegationGrant, run.grant_id)
    if grant is None or grant.user_id != run.user_id:
        raise AuthorizationError("GRANT_NOT_ACTIVE", "The run grant is unavailable", 403)
    return CommerceRunContext(
        run.id,
        run.user_id,
        run.agent_id,
        run.grant_id,
        grant.immutable_digest,
        grant.auto_execute,
        run.payment_mode,
    )


@app.get("/v1/agent-runs/{run_id}/quote", response_model=QuoteOut)
async def get_run_quote(
    run_id: uuid.UUID, user: User = Depends(agent_user), session: Session = Depends(get_session)
) -> QuoteOut:
    run = session.get(AgentRun, run_id)
    if run is None or run.user_id != user.id or run.active_quote_id is None:
        raise TrustCartError("QUOTE_NOT_FOUND", "This run has no canonical quote", 404)
    quote_id = run.active_quote_id
    context = _run_context(run, session)
    session.rollback()
    result = await merchant_request(context, "GET", f"/v1/quotes/{quote_id}")
    return QuoteOut.model_validate(result)


@app.get("/v1/agent-runs/{run_id}/checkout", response_model=CheckoutOut)
async def get_run_checkout(
    run_id: uuid.UUID, user: User = Depends(agent_user), session: Session = Depends(get_session)
) -> CheckoutOut:
    run = session.get(AgentRun, run_id)
    if run is None or run.user_id != user.id or run.checkout_id is None:
        raise TrustCartError("CHECKOUT_NOT_FOUND", "This run has no Checkout", 404)
    checkout_id = run.checkout_id
    context = _run_context(run, session)
    session.rollback()
    result = await merchant_request(context, "GET", f"/v1/checkouts/{checkout_id}")
    return CheckoutOut.model_validate(result)


@app.post("/v1/agent-runs/{run_id}/cancel", response_model=AgentRunOut)
async def cancel_run(
    run_id: uuid.UUID, user: User = Depends(agent_user), session: Session = Depends(get_session)
) -> AgentRunOut:
    run = session.scalar(select(AgentRun).where(AgentRun.id == run_id).with_for_update())
    if run is None or run.user_id != user.id:
        raise TrustCartError("RUN_NOT_FOUND", "Agent run was not found", 404)
    checkout_id = run.checkout_id
    context = _run_context(run, session) if checkout_id is not None else None
    if run.status in {RunStatus.QUEUED, RunStatus.RUNNING}:
        run.status = RunStatus.CANCELLED
    session.commit()
    if checkout_id is not None:
        assert context is not None
        try:
            await merchant_request(
                context, "POST", f"/v1/checkouts/{checkout_id}/cancel"
            )
        except TrustCartError as exc:
            if exc.code != "CHECKOUT_NOT_CANCELLABLE":
                raise
    session.refresh(run)
    return AgentRunOut.model_validate(run)
