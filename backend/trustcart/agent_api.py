from __future__ import annotations

import asyncio
import json
import uuid

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .agent_runtime import create_signed_grant_request, run_commerce_agent
from .auth import agent_user
from .config import get_settings
from .db import SessionLocal, get_session
from .enums import RunStatus
from .errors import AuthorizationError, TrustCartError
from .models import AgentRun, AgentToolEvent, DelegationGrant, RegisteredAgent, User
from .schemas import AgentRunCreate, AgentRunOut, GrantRequestCreate

settings = get_settings()
app = FastAPI(title="TrustCart Buyer Agent API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin],
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
        "model": settings.model_name,
        "openai_configured": bool(settings.openai_api_key),
    }


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
    if grant is None or grant.user_id != user.id or grant.status != "ACTIVE":
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
        reasoning_effort=settings.model_reasoning_effort,
    )
    session.add(run)
    session.commit()
    await run_commerce_agent(run.id)
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
                yield f"event: state\ndata: {json.dumps({'status': run.status, 'checkout_id': str(run.checkout_id) if run.checkout_id else None})}\n\n"
                if run.status not in {RunStatus.QUEUED, RunStatus.RUNNING}:
                    return
            await asyncio.sleep(0.5)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/v1/agent-runs/{run_id}/cancel", response_model=AgentRunOut)
def cancel_run(
    run_id: uuid.UUID, user: User = Depends(agent_user), session: Session = Depends(get_session)
) -> AgentRunOut:
    run = session.scalar(select(AgentRun).where(AgentRun.id == run_id).with_for_update())
    if run is None or run.user_id != user.id:
        raise TrustCartError("RUN_NOT_FOUND", "Agent run was not found", 404)
    if run.status in {RunStatus.QUEUED, RunStatus.RUNNING}:
        run.status = RunStatus.CANCELLED
        session.commit()
    return AgentRunOut.model_validate(run)
