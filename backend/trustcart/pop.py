from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from cryptography.exceptions import InvalidSignature
from fastapi import Header, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from .config import get_settings
from .crypto import build_proof_content, public_key_from_jwk, sha256_hex, verify_es256
from .db import SessionLocal
from .errors import AuthorizationError, ConflictError
from .models import DelegationGrant, ProofNonce, RegisteredAgent

settings = get_settings()


@dataclass(frozen=True, slots=True)
class AgentPrincipal:
    agent_id: UUID
    grant_id: UUID
    user_id: UUID
    merchant_id: UUID
    grant_digest: str
    allowed_categories: tuple[str, ...]
    auto_execute: bool


@dataclass(frozen=True, slots=True)
class RegisteredAgentPrincipal:
    agent_id: UUID
    merchant_id: UUID


async def require_registered_agent_proof(
    request: Request,
    x_agent_id: str = Header(alias="X-Agent-Id"),
    x_agent_timestamp: str = Header(alias="X-Agent-Timestamp"),
    x_agent_nonce: str = Header(alias="X-Agent-Nonce"),
    x_body_sha256: str = Header(alias="X-Body-SHA256"),
    x_agent_signature: str = Header(alias="X-Agent-Signature"),
) -> RegisteredAgentPrincipal:
    """Verify a TC-AGENT-V1 proof before a delegation grant exists."""
    try:
        agent_id, issued = UUID(x_agent_id), int(x_agent_timestamp)
    except (ValueError, TypeError) as exc:
        raise AuthorizationError(
            "INVALID_PROOF_HEADERS", "Malformed agent proof headers", 401
        ) from exc
    now_epoch = int(time.time())
    if abs(now_epoch - issued) > settings.pop_clock_skew_seconds:
        raise AuthorizationError(
            "PROOF_EXPIRED", "Agent proof is outside the accepted clock window", 401
        )
    if not 16 <= len(x_agent_nonce) <= 128:
        raise AuthorizationError("INVALID_NONCE", "Nonce length is invalid", 401)
    raw_body = await request.body()
    body_hash = sha256_hex(raw_body)
    if body_hash != x_body_sha256.lower():
        raise AuthorizationError("BODY_DIGEST_MISMATCH", "Signed body digest does not match", 401)
    with SessionLocal() as session:
        agent = session.get(RegisteredAgent, agent_id)
        if agent is None or agent.status != "ACTIVE":
            raise AuthorizationError("AGENT_NOT_ACTIVE", "Agent registration is not active", 403)
        content = build_proof_content(
            method=request.method,
            path=request.url.path,
            query_items=list(request.query_params.multi_items()),
            body_sha256=body_hash,
            timestamp=issued,
            nonce=x_agent_nonce,
        )
        try:
            verify_es256(public_key_from_jwk(agent.public_jwk), content, x_agent_signature)
        except (ValueError, InvalidSignature) as exc:
            raise AuthorizationError(
                "INVALID_SIGNATURE", "Agent proof signature is invalid", 401
            ) from exc
        session.add(
            ProofNonce(
                agent_id=agent.id,
                nonce=x_agent_nonce,
                issued_at=datetime.fromtimestamp(issued, UTC),
                expires_at=datetime.now(UTC) + timedelta(seconds=settings.nonce_ttl_seconds),
            )
        )
        try:
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            raise ConflictError(
                "PROOF_REPLAYED", "This proof nonce has already been consumed", 409
            ) from exc
        return RegisteredAgentPrincipal(agent.id, agent.merchant_id)


async def require_agent_proof(
    request: Request,
    x_agent_id: str = Header(alias="X-Agent-Id"),
    x_grant_id: str = Header(alias="X-Grant-Id"),
    x_agent_timestamp: str = Header(alias="X-Agent-Timestamp"),
    x_agent_nonce: str = Header(alias="X-Agent-Nonce"),
    x_body_sha256: str = Header(alias="X-Body-SHA256"),
    x_agent_signature: str = Header(alias="X-Agent-Signature"),
) -> AgentPrincipal:
    """Verify PoP and durably consume the nonce before business logic executes."""
    try:
        agent_id, grant_id = UUID(x_agent_id), UUID(x_grant_id)
        issued = int(x_agent_timestamp)
    except (ValueError, TypeError) as exc:
        raise AuthorizationError(
            "INVALID_PROOF_HEADERS", "Malformed agent proof headers", 401
        ) from exc

    now_epoch = int(time.time())
    if abs(now_epoch - issued) > settings.pop_clock_skew_seconds:
        raise AuthorizationError(
            "PROOF_EXPIRED", "Agent proof is outside the accepted clock window", 401
        )
    if not 16 <= len(x_agent_nonce) <= 128:
        raise AuthorizationError("INVALID_NONCE", "Nonce length is invalid", 401)

    raw_body = await request.body()
    body_hash = sha256_hex(raw_body)
    if body_hash != x_body_sha256.lower():
        raise AuthorizationError("BODY_DIGEST_MISMATCH", "Signed body digest does not match", 401)

    with SessionLocal() as session:
        agent = session.get(RegisteredAgent, agent_id)
        grant = session.get(DelegationGrant, grant_id)
        now = datetime.now(UTC)
        if agent is None or agent.status != "ACTIVE":
            raise AuthorizationError("AGENT_NOT_ACTIVE", "Agent registration is not active", 403)
        if grant is None or grant.agent_id != agent.id:
            raise AuthorizationError(
                "GRANT_AGENT_MISMATCH", "Grant is not bound to this agent", 403
            )
        if grant.status != "ACTIVE" or not (grant.valid_from <= now < grant.expires_at):
            raise AuthorizationError(
                "GRANT_NOT_ACTIVE", "Grant is revoked, expired, or not yet valid", 403
            )

        canonical = build_proof_content(
            method=request.method,
            path=request.url.path,
            query_items=list(request.query_params.multi_items()),
            body_sha256=body_hash,
            timestamp=issued,
            nonce=x_agent_nonce,
            grant_id=str(grant.id),
            grant_digest=grant.immutable_digest,
        )
        try:
            verify_es256(public_key_from_jwk(agent.public_jwk), canonical, x_agent_signature)
        except (ValueError, InvalidSignature) as exc:
            raise AuthorizationError(
                "INVALID_SIGNATURE", "Agent proof signature is invalid", 401
            ) from exc

        session.add(
            ProofNonce(
                agent_id=agent.id,
                nonce=x_agent_nonce,
                issued_at=datetime.fromtimestamp(issued, UTC),
                expires_at=now + timedelta(seconds=settings.nonce_ttl_seconds),
            )
        )
        try:
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            raise ConflictError(
                "PROOF_REPLAYED", "This proof nonce has already been consumed", 409
            ) from exc

        return AgentPrincipal(
            agent_id=agent.id,
            grant_id=grant.id,
            user_id=grant.user_id,
            merchant_id=grant.merchant_id,
            grant_digest=grant.immutable_digest,
            allowed_categories=tuple(grant.allowed_categories),
            auto_execute=grant.auto_execute,
        )


def prune_expired_nonces(batch_size: int = 1_000) -> int:
    """Delete a bounded nonce batch; safe to call once per worker minute."""
    with SessionLocal.begin() as session:
        ids = session.execute(
            select(ProofNonce.agent_id, ProofNonce.nonce)
            .where(ProofNonce.expires_at < datetime.now(UTC))
            .limit(batch_size)
        ).all()
        for agent_id, nonce in ids:
            obj = session.get(ProofNonce, (agent_id, nonce))
            if obj is not None:
                session.delete(obj)
        return len(ids)
