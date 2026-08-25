from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from .crypto import canonical_json, sha256_hex
from .models import AuditEvent


@dataclass(slots=True)
class AuditFact:
    aggregate_type: str
    aggregate_id: uuid.UUID
    layer: str
    actor: str
    action: str
    reason_code: str
    explanation: str
    checkout_id: uuid.UUID | None = None
    input_digest: str | None = None
    amount_delta_paise: int = 0
    data: dict[str, Any] = field(default_factory=dict)
    trace_id: str | None = None
    correlation_id: str | None = None


def append_audit(session: Session, fact: AuditFact) -> AuditEvent:
    previous = None
    sequence = 1
    if fact.checkout_id:
        previous = session.scalar(
            select(AuditEvent)
            .where(AuditEvent.checkout_id == fact.checkout_id)
            .order_by(desc(AuditEvent.sequence))
            .limit(1)
        )
        if previous:
            sequence = previous.sequence + 1
    payload = {
        "aggregate_type": fact.aggregate_type,
        "aggregate_id": str(fact.aggregate_id),
        "checkout_id": str(fact.checkout_id) if fact.checkout_id else None,
        "sequence": sequence,
        "layer": fact.layer,
        "actor": fact.actor,
        "action": fact.action,
        "reason_code": fact.reason_code,
        "explanation": fact.explanation,
        "input_digest": fact.input_digest,
        "amount_delta_paise": fact.amount_delta_paise,
        "data": fact.data,
        "previous_hash": previous.event_hash if previous else None,
    }
    event = AuditEvent(
        **payload,
        trace_id=fact.trace_id,
        correlation_id=fact.correlation_id,
        event_hash=sha256_hex(canonical_json(payload)),
    )
    session.add(event)
    session.flush()
    return event
