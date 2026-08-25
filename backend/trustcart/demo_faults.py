from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import SessionLocal
from .errors import TrustCartError
from .models import DemoFault

SUPPORTED_DEMO_FAULTS = {
    "DROP_ORDER_CREATE_RESPONSE",
    "FORCE_MODEL_TIMEOUT",
}


def set_demo_fault(
    session: Session, key: str, *, armed: bool, user_id: uuid.UUID
) -> DemoFault:
    if key not in SUPPORTED_DEMO_FAULTS:
        raise TrustCartError("FAULT_NOT_SUPPORTED", "Unknown demo failure fixture", 404)
    row = session.scalar(select(DemoFault).where(DemoFault.key == key).with_for_update())
    if row is None:
        row = DemoFault(key=key)
        session.add(row)
    row.armed = armed
    row.armed_by_user_id = user_id if armed else None
    row.armed_at = datetime.now(UTC) if armed else None
    if armed:
        row.consumed_at = None
    return row


def consume_demo_fault(key: str) -> bool:
    """Atomically consume one test-only fault so concurrency cannot trigger it twice."""
    with SessionLocal.begin() as session:
        row = session.scalar(select(DemoFault).where(DemoFault.key == key).with_for_update())
        if row is None or not row.armed:
            return False
        row.armed = False
        row.consumed_at = datetime.now(UTC)
        return True
