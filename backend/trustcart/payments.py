from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from datetime import UTC, datetime
from typing import Any

import razorpay
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from .audit import AuditFact, append_audit
from .commerce import settle_reservations
from .config import Settings
from .crypto import sha256_hex
from .enums import CheckoutStatus, WebhookStatus
from .errors import AuthorizationError, TrustCartError
from .models import Checkout, PaymentAttempt, RazorpayOrder, WebhookEvent


def provider_facts_match(
    expected_amount_paise: int,
    expected_currency: str,
    observed_amount_paise: int,
    observed_currency: str,
) -> bool:
    """Money state may advance only when provider facts match the canonical Checkout."""
    return (
        observed_amount_paise == expected_amount_paise
        and observed_currency == expected_currency
    )


def verify_razorpay_webhook(raw_body: bytes, signature: str, secrets: list[str]) -> bool:
    for secret in secrets:
        expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
        if hmac.compare_digest(expected, signature):
            return True
    return False


def razorpay_client(settings: Settings) -> razorpay.Client:
    if not settings.razorpay_key_id or not settings.razorpay_key_secret:
        raise TrustCartError(
            "RAZORPAY_NOT_CONFIGURED", "Razorpay Test Mode credentials are not configured", 503
        )
    return razorpay.Client(
        auth=(
            settings.razorpay_key_id.get_secret_value(),
            settings.razorpay_key_secret.get_secret_value(),
        )
    )


def create_test_order(settings: Settings, checkout: Checkout) -> dict[str, Any]:
    return razorpay_client(settings).order.create(
        {
            "amount": checkout.amount_paise,
            "currency": checkout.currency,
            "receipt": checkout.receipt,
            "notes": {"trustcart_checkout_id": str(checkout.id)},
        }
    )


def find_order_by_receipt(settings: Settings, receipt: str) -> dict[str, Any] | None:
    result = razorpay_client(settings).order.all({"receipt": receipt, "count": 10})
    matches = [item for item in result.get("items", []) if item.get("receipt") == receipt]
    if len(matches) > 1:
        raise TrustCartError(
            "PROVIDER_DUPLICATE_RECEIPT", "More than one provider order uses this receipt", 502
        )
    return matches[0] if matches else None


def persist_webhook(
    session: Session,
    raw_body: bytes,
    signature: str,
    event_id: str,
    settings: Settings,
) -> tuple[WebhookEvent, bool]:
    secrets = [
        value.get_secret_value()
        for value in (settings.razorpay_webhook_secret, settings.razorpay_previous_webhook_secret)
        if value
    ]
    if not secrets or not verify_razorpay_webhook(raw_body, signature, secrets):
        raise AuthorizationError(
            "INVALID_WEBHOOK_SIGNATURE", "Razorpay webhook signature is invalid", 401
        )
    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        raise TrustCartError("INVALID_WEBHOOK_JSON", "Webhook body is not valid JSON", 400) from exc
    event_uuid = uuid.uuid4()
    inserted_id = session.execute(
        pg_insert(WebhookEvent)
        .values(
            id=event_uuid,
            provider="razorpay",
            provider_event_id=event_id,
            event_type=str(payload.get("event", "unknown")),
            payload_digest=sha256_hex(raw_body),
            sanitized_payload=sanitize_webhook(payload),
            status=WebhookStatus.RECEIVED.value,
        )
        .on_conflict_do_nothing(constraint="uq_webhook_event")
        .returning(WebhookEvent.id)
    ).scalar_one_or_none()
    if inserted_id is None:
        existing = session.scalar(
            select(WebhookEvent).where(
                WebhookEvent.provider == "razorpay",
                WebhookEvent.provider_event_id == event_id,
            )
        )
        assert existing is not None
        return existing, False
    event = session.get(WebhookEvent, inserted_id)
    assert event is not None
    return event, True


def sanitize_webhook(payload: dict[str, Any]) -> dict[str, Any]:
    payment = payload.get("payload", {}).get("payment", {}).get("entity", {})
    order = payload.get("payload", {}).get("order", {}).get("entity", {})
    return {
        "event": payload.get("event"),
        "payment": {
            key: payment.get(key)
            for key in (
                "id",
                "order_id",
                "amount",
                "currency",
                "status",
                "captured",
                "error_code",
                "error_description",
                "created_at",
            )
        },
        "order": {key: order.get(key) for key in ("id", "receipt", "amount", "currency", "status")},
    }


def process_webhook(session: Session, event: WebhookEvent) -> None:
    if event.status == WebhookStatus.PROCESSED:
        return
    payment = event.sanitized_payload.get("payment", {})
    order_payload = event.sanitized_payload.get("order", {})
    provider_order_id = payment.get("order_id") or order_payload.get("id")
    order = session.scalar(
        select(RazorpayOrder).where(RazorpayOrder.razorpay_order_id == provider_order_id)
    )
    if order is None:
        event.status = WebhookStatus.IGNORED
        event.error_detail = "No local order is bound to this provider order"
        event.processed_at = datetime.now(UTC)
        return
    checkout = session.scalar(
        select(Checkout).where(Checkout.id == order.checkout_id).with_for_update()
    )
    assert checkout is not None
    event_type = event.event_type
    payment_id = payment.get("id")
    if payment_id:
        attempt = session.scalar(
            select(PaymentAttempt).where(PaymentAttempt.provider_payment_id == payment_id)
        )
        if attempt is None:
            attempt = PaymentAttempt(
                checkout_id=checkout.id,
                razorpay_order_id=order.id,
                provider_payment_id=payment_id,
                provider_status=str(payment.get("status") or "unknown"),
                amount_paise=int(payment.get("amount") or checkout.amount_paise),
                currency=str(payment.get("currency") or checkout.currency),
                captured=bool(payment.get("captured")),
                error_code=payment.get("error_code"),
                error_description=payment.get("error_description"),
                provider_created_at=(
                    datetime.fromtimestamp(payment["created_at"], UTC)
                    if payment.get("created_at")
                    else None
                ),
            )
            session.add(attempt)
        else:
            attempt.provider_status = str(payment.get("status") or attempt.provider_status)
            attempt.captured = bool(payment.get("captured")) or attempt.captured

    if event_type in {"payment.captured", "order.paid"}:
        observed_amount = int(payment.get("amount") or order_payload.get("amount") or 0)
        observed_currency = str(
            payment.get("currency") or order_payload.get("currency") or ""
        )
        if checkout.status == CheckoutStatus.EXPIRED:
            checkout.status = CheckoutStatus.LATE_CAPTURE_INCIDENT
            checkout.version += 1
            append_audit(
                session,
                AuditFact(
                    aggregate_type="checkout",
                    aggregate_id=checkout.id,
                    checkout_id=checkout.id,
                    layer="recovery",
                    actor="razorpay-webhook",
                    action="late_capture.detected",
                    reason_code="LATE_CAPTURE_AFTER_RELEASE",
                    explanation="A capture arrived after inventory and allowance were released; manual compensation is required.",
                    data={"event_id": event.provider_event_id},
                ),
            )
        elif not provider_facts_match(
            checkout.amount_paise,
            checkout.currency,
            observed_amount,
            observed_currency,
        ):
            checkout.status = CheckoutStatus.RECONCILING
            checkout.last_error_code = "PROVIDER_PAYMENT_FACT_MISMATCH"
            checkout.last_error_detail = (
                f"Expected {checkout.amount_paise} {checkout.currency}; "
                f"observed {observed_amount} {observed_currency}"
            )
            checkout.next_retry_at = datetime.now(UTC)
            checkout.version += 1
            append_audit(
                session,
                AuditFact(
                    aggregate_type="checkout",
                    aggregate_id=checkout.id,
                    checkout_id=checkout.id,
                    layer="recovery",
                    actor="razorpay-webhook",
                    action="payment.fact_mismatch",
                    reason_code="PROVIDER_PAYMENT_FACT_MISMATCH",
                    explanation="The captured payment facts did not match the canonical Checkout; settlement was blocked for reconciliation.",
                    data={"event_id": event.provider_event_id},
                ),
            )
        elif checkout.status not in {CheckoutStatus.PAID, CheckoutStatus.SIMULATED_SETTLED}:
            settle_reservations(session, checkout, CheckoutStatus.PAID)
            order.status = "PAID"
    elif event_type == "payment.failed":
        # A failed attempt is evidence, not proof that the Order can never be captured.
        append_audit(
            session,
            AuditFact(
                aggregate_type="checkout",
                aggregate_id=checkout.id,
                checkout_id=checkout.id,
                layer="execution",
                actor="razorpay-webhook",
                action="payment.attempt_failed",
                reason_code="PAYMENT_ATTEMPT_FAILED_RESERVATION_RETAINED",
                explanation="The attempt failed, but Order-level reservations remain held for a possible later capture.",
                data={"payment_id": payment_id},
            ),
        )
    event.status = WebhookStatus.PROCESSED
    event.processed_at = datetime.now(UTC)
