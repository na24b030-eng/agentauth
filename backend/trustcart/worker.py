from __future__ import annotations

import secrets
import time
import uuid
from datetime import UTC, datetime, timedelta

import razorpay
from sqlalchemy import and_, delete, or_, select, text

from .audit import AuditFact, append_audit
from .commerce import release_reservations, settle_reservations
from .config import get_settings
from .db import SessionLocal
from .enums import CheckoutStatus, PaymentMode
from .models import Checkout, ProofNonce, RazorpayOrder
from .payments import create_test_order, find_order_by_receipt, provider_facts_match

settings = get_settings()


def claim_due_execution(limit: int = 20) -> list[str]:
    now = datetime.now(UTC)
    with SessionLocal.begin() as session:
        rows = session.scalars(
            select(Checkout)
            .where(Checkout.status == CheckoutStatus.CANCEL_WINDOW, Checkout.execute_after <= now)
            .order_by(Checkout.execute_after)
            .limit(limit)
            .with_for_update(skip_locked=True)
        ).all()
        ids: list[str] = []
        for checkout in rows:
            checkout.status = CheckoutStatus.ORDER_CREATING
            checkout.version += 1
            ids.append(str(checkout.id))
            append_audit(
                session,
                AuditFact(
                    "checkout",
                    checkout.id,
                    "execution",
                    "agentauth-worker",
                    "execution.started",
                    "CANCEL_WINDOW_ELAPSED",
                    "The cancellation window elapsed; execution may begin.",
                    checkout_id=checkout.id,
                ),
            )
        return ids


def execute_checkout(checkout_id: str) -> None:
    checkout_uuid = uuid.UUID(checkout_id)
    with SessionLocal() as read_session:
        checkout = read_session.get(Checkout, checkout_uuid)
        if checkout is None or checkout.status != CheckoutStatus.ORDER_CREATING:
            return
        mode = checkout.payment_mode

    if mode == PaymentMode.DELEGATED_DEBIT_SIMULATOR:
        with SessionLocal.begin() as session:
            checkout = session.scalar(
                select(Checkout).where(Checkout.id == checkout_uuid).with_for_update()
            )
            if checkout and checkout.status == CheckoutStatus.ORDER_CREATING:
                settle_reservations(session, checkout, CheckoutStatus.SIMULATED_SETTLED)
        return

    with SessionLocal.begin() as session:
        checkout = session.scalar(
            select(Checkout).where(Checkout.id == checkout_uuid).with_for_update()
        )
        if checkout is None or checkout.status != CheckoutStatus.ORDER_CREATING:
            return
        local_order = session.scalar(
            select(RazorpayOrder).where(RazorpayOrder.checkout_id == checkout.id)
        )
        if local_order is None:
            local_order = RazorpayOrder(
                checkout_id=checkout.id,
                receipt=checkout.receipt,
                amount_paise=checkout.amount_paise,
                currency=checkout.currency,
            )
            session.add(local_order)

    try:
        provider_order = create_test_order(settings, checkout)
        if settings.fault_drop_order_response:
            raise ConnectionError("fault injection: successful provider response discarded")
    except razorpay.errors.BadRequestError as exc:
        with SessionLocal.begin() as session:
            checkout = session.scalar(
                select(Checkout).where(Checkout.id == checkout_uuid).with_for_update()
            )
            if checkout and checkout.status == CheckoutStatus.ORDER_CREATING:
                checkout.last_error_code = "PROVIDER_REJECTED_ORDER"
                checkout.last_error_detail = str(exc)[:500]
                release_reservations(
                    session,
                    checkout,
                    CheckoutStatus.FAILED_TERMINAL,
                    "PROVIDER_REJECTED_ORDER",
                )
        return
    except Exception as exc:
        with SessionLocal.begin() as session:
            checkout = session.scalar(
                select(Checkout).where(Checkout.id == checkout_uuid).with_for_update()
            )
            if checkout and checkout.status == CheckoutStatus.ORDER_CREATING:
                checkout.status = CheckoutStatus.RECONCILING
                checkout.last_error_code = "ORDER_CREATE_AMBIGUOUS"
                checkout.last_error_detail = str(exc)[:500]
                checkout.next_retry_at = datetime.now(UTC) + timedelta(seconds=5)
                checkout.version += 1
                append_audit(
                    session,
                    AuditFact(
                        "checkout",
                        checkout.id,
                        "recovery",
                        "agentauth-worker",
                        "order_create.ambiguous",
                        "ORDER_CREATE_RESPONSE_LOST",
                        "The create response was ambiguous; the stable receipt will be reconciled before any retry.",
                        checkout_id=checkout.id,
                    ),
                )
        return

    with SessionLocal.begin() as session:
        checkout = session.scalar(
            select(Checkout).where(Checkout.id == checkout_uuid).with_for_update()
        )
        local_order = (
            session.scalar(select(RazorpayOrder).where(RazorpayOrder.checkout_id == checkout.id))
            if checkout
            else None
        )
        if checkout and local_order and checkout.status == CheckoutStatus.ORDER_CREATING:
            local_order.razorpay_order_id = provider_order["id"]
            local_order.status = str(provider_order.get("status", "created")).upper()
            checkout.status = CheckoutStatus.PAYMENT_PENDING
            checkout.version += 1
            append_audit(
                session,
                AuditFact(
                    "checkout",
                    checkout.id,
                    "execution",
                    "razorpay-orders-api",
                    "order.created",
                    "PROVIDER_ORDER_CONFIRMED",
                    "A real Razorpay Test Mode Order is ready for Standard Checkout.",
                    checkout_id=checkout.id,
                    data={"razorpay_order_id": provider_order["id"]},
                ),
            )


def claim_reconciliation(limit: int = 20) -> list[tuple[str, str, int]]:
    now = datetime.now(UTC)
    with SessionLocal.begin() as session:
        rows = session.scalars(
            select(Checkout)
            .where(
                or_(
                    Checkout.status.in_(
                        [CheckoutStatus.RECONCILING, CheckoutStatus.EXPIRING]
                    ),
                    and_(
                        Checkout.status == CheckoutStatus.ORDER_CREATING,
                        Checkout.updated_at <= now - timedelta(seconds=30),
                    ),
                ),
                (Checkout.next_retry_at.is_(None)) | (Checkout.next_retry_at <= now),
            )
            .order_by(Checkout.updated_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        ).all()
        claims = []
        for checkout in rows:
            token = secrets.token_hex(24)
            checkout.status = CheckoutStatus.RECONCILING
            checkout.reconciliation_token = token
            checkout.version += 1
            checkout.reconciliation_version = checkout.version
            claims.append((str(checkout.id), token, checkout.version))
        return claims


def reconcile_checkout(checkout_id: str, token: str, expected_version: int) -> None:
    checkout_uuid = uuid.UUID(checkout_id)
    with SessionLocal() as session:
        checkout = session.get(Checkout, checkout_uuid)
        if checkout is None:
            return
        receipt = checkout.receipt
    try:
        observation = find_order_by_receipt(settings, receipt)
        observed_at = datetime.now(UTC)
        observation_trusted = True
        observation_error = None
    except Exception as exc:
        observation, observed_at = None, None
        observation_trusted = False
        observation_error = str(exc)[:500]

    with SessionLocal.begin() as session:
        checkout = session.scalar(
            select(Checkout).where(Checkout.id == checkout_uuid).with_for_update()
        )
        if (
            checkout is None
            or checkout.reconciliation_token != token
            or checkout.version != expected_version
        ):
            return
        if not observation_trusted:
            checkout.last_error_code = "PROVIDER_OBSERVATION_UNTRUSTED"
            checkout.last_error_detail = observation_error
            checkout.next_retry_at = datetime.now(UTC) + timedelta(seconds=30)
            checkout.version += 1
            checkout.reconciliation_token = None
            return
        checkout.provider_observed_at = observed_at
        local_order = session.scalar(
            select(RazorpayOrder).where(RazorpayOrder.checkout_id == checkout.id)
        )
        if observation:
            if local_order is None:
                local_order = RazorpayOrder(
                    checkout_id=checkout.id,
                    receipt=checkout.receipt,
                    amount_paise=checkout.amount_paise,
                    currency=checkout.currency,
                )
                session.add(local_order)
            local_order.razorpay_order_id = observation["id"]
            local_order.status = str(observation.get("status", "created")).upper()
            observed_amount = int(observation.get("amount") or 0)
            observed_currency = str(observation.get("currency") or "")
            if not provider_facts_match(
                checkout.amount_paise,
                checkout.currency,
                observed_amount,
                observed_currency,
            ):
                first_mismatch = checkout.last_error_code != "PROVIDER_ORDER_FACT_MISMATCH"
                checkout.last_error_code = "PROVIDER_ORDER_FACT_MISMATCH"
                checkout.last_error_detail = (
                    f"Expected {checkout.amount_paise} {checkout.currency}; "
                    f"observed {observed_amount} {observed_currency}"
                )
                checkout.next_retry_at = datetime.now(UTC) + timedelta(seconds=30)
                checkout.version += 1
                checkout.reconciliation_token = None
                if first_mismatch:
                    append_audit(
                        session,
                        AuditFact(
                            "checkout",
                            checkout.id,
                            "recovery",
                            "agentauth-worker",
                            "order.fact_mismatch",
                            "PROVIDER_ORDER_FACT_MISMATCH",
                            "Provider order amount or currency did not match the canonical Checkout; settlement was blocked.",
                            checkout_id=checkout.id,
                        ),
                    )
                return
            if observation.get("status") == "paid":
                settle_reservations(session, checkout, CheckoutStatus.PAID)
            elif datetime.now(UTC) >= checkout.late_capture_grace_until:
                release_reservations(
                    session,
                    checkout,
                    CheckoutStatus.EXPIRED,
                    "PROVIDER_CONFIRMED_UNPAID_AFTER_GRACE",
                )
            else:
                checkout.status = CheckoutStatus.PAYMENT_PENDING
                checkout.version += 1
                checkout.reconciliation_token = None
                append_audit(
                    session,
                    AuditFact(
                        "checkout",
                        checkout.id,
                        "recovery",
                        "agentauth-worker",
                        "order.recovered",
                        "ORDER_FOUND_BY_RECEIPT",
                        "The lost create response was recovered by the stable receipt without creating a second Order.",
                        checkout_id=checkout.id,
                        data={"razorpay_order_id": observation["id"]},
                    ),
                )
        elif datetime.now(UTC) >= checkout.late_capture_grace_until:
            release_reservations(
                session, checkout, CheckoutStatus.EXPIRED, "PROVIDER_CONFIRMED_UNPAID_AFTER_GRACE"
            )
        else:
            checkout.next_retry_at = datetime.now(UTC) + timedelta(seconds=30)
            checkout.version += 1
            checkout.reconciliation_token = None


def expire_pending(limit: int = 20) -> int:
    now = datetime.now(UTC)
    with SessionLocal.begin() as session:
        rows = session.scalars(
            select(Checkout)
            .where(
                Checkout.status == CheckoutStatus.PAYMENT_PENDING,
                Checkout.payment_deadline_at <= now,
            )
            .limit(limit)
            .with_for_update(skip_locked=True)
        ).all()
        for checkout in rows:
            checkout.status = CheckoutStatus.EXPIRING
            checkout.next_retry_at = now
            checkout.version += 1
        return len(rows)


def prune_nonces(limit: int = 1_000) -> int:
    with SessionLocal.begin() as session:
        # Singleton maintenance without preventing horizontally-scaled queue consumers.
        if not session.scalar(text("SELECT pg_try_advisory_xact_lock(84920317)")):
            return 0
        keys = session.execute(
            select(ProofNonce.agent_id, ProofNonce.nonce)
            .where(ProofNonce.expires_at < datetime.now(UTC))
            .limit(limit)
        ).all()
        for key in keys:
            session.execute(
                delete(ProofNonce).where(
                    ProofNonce.agent_id == key.agent_id, ProofNonce.nonce == key.nonce
                )
            )
        return len(keys)


def tick() -> None:
    for checkout_id in claim_due_execution():
        execute_checkout(checkout_id)
    expire_pending()
    for checkout_id, token, version in claim_reconciliation():
        reconcile_checkout(checkout_id, token, version)


def main() -> None:
    last_reconcile = last_prune = 0.0
    while True:
        now = time.monotonic()
        for checkout_id in claim_due_execution():
            execute_checkout(checkout_id)
        if now - last_reconcile >= 30:
            expire_pending()
            for checkout_id, token, version in claim_reconciliation():
                reconcile_checkout(checkout_id, token, version)
            last_reconcile = now
        if now - last_prune >= 60:
            prune_nonces()
            last_prune = now
        time.sleep(2)


if __name__ == "__main__":
    main()
