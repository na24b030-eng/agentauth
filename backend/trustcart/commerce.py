from __future__ import annotations

import random
import time
import uuid
from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import TypeVar

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, selectinload

from .audit import AuditFact, append_audit
from .config import get_settings
from .crypto import canonical_json, sha256_hex
from .enums import CheckoutStatus, QuoteStatus, ReservationStatus
from .errors import AuthorizationError, ConflictError, TrustCartError
from .models import (
    AllowanceReservation,
    Checkout,
    DelegationGrant,
    Inventory,
    InventoryReservation,
    Product,
    Quote,
    QuoteItem,
)
from .pop import AgentPrincipal
from .schemas import CheckoutCreate, CheckoutOut, QuoteCreate, QuoteItemOut, QuoteOut

T = TypeVar("T")
settings = get_settings()

DELIVERY_OPTIONS = {
    "tonight": {"label": "Tonight", "window": "18:00–21:00", "fee_paise": 4900},
    "express": {"label": "Express", "window": "45–60 minutes", "fee_paise": 7900},
    "tomorrow": {"label": "Tomorrow morning", "window": "08:00–11:00", "fee_paise": 2900},
}


def utcnow() -> datetime:
    return datetime.now(UTC)


def transaction_retry(fn: Callable[[], T], attempts: int = 3) -> T:
    """Retry database-only work for PostgreSQL deadlock/serialization errors."""
    for attempt in range(attempts):
        try:
            return fn()
        except DBAPIError as exc:
            code = getattr(getattr(exc, "orig", None), "sqlstate", None)
            if code not in {"40P01", "40001"} or attempt == attempts - 1:
                raise
            time.sleep(random.uniform(0.01, 0.05) * (2**attempt))
    raise AssertionError("unreachable")


def semantic_checkout_hash(principal: AgentPrincipal, payload: CheckoutCreate) -> str:
    return sha256_hex(
        canonical_json(
            {
                "operation": "checkout.create.v1",
                "merchant_id": str(principal.merchant_id),
                "agent_id": str(principal.agent_id),
                "grant_id": str(principal.grant_id),
                "quote_id": str(payload.quote_id),
                "payment_mode": payload.payment_mode.value,
            }
        )
    )


def create_quote(session: Session, principal: AgentPrincipal, payload: QuoteCreate) -> QuoteOut:
    quantities: dict[str, int] = defaultdict(int)
    for item in payload.items:
        quantities[item.sku] += item.quantity
    option = DELIVERY_OPTIONS.get(payload.delivery_option_id)
    if option is None:
        raise TrustCartError("DELIVERY_OPTION_INVALID", "Delivery option is not available", 422)

    products = session.scalars(
        select(Product).where(
            Product.merchant_id == principal.merchant_id,
            Product.sku.in_(quantities),
            Product.active.is_(True),
        )
    ).all()
    # The Product model intentionally owns no reverse relationship; load inventory explicitly.
    by_sku = {product.sku: product for product in products}
    if set(by_sku) != set(quantities):
        raise TrustCartError("PRODUCT_NOT_FOUND", "One or more SKUs are unavailable", 422)

    inventories = {
        inv.product_id: inv
        for inv in session.scalars(
            select(Inventory).where(Inventory.product_id.in_([p.id for p in products]))
        )
    }
    lines: list[tuple[Product, int]] = []
    for sku in sorted(quantities):
        product, quantity = by_sku[sku], quantities[sku]
        if product.category not in principal.allowed_categories:
            raise AuthorizationError(
                "CATEGORY_NOT_ALLOWED", f"Grant does not allow {product.category}", 403
            )
        inv = inventories[product.id]
        if inv.on_hand_qty - inv.reserved_qty < quantity:
            raise ConflictError(
                "INSUFFICIENT_INVENTORY", f"Insufficient inventory for {product.name}", 409
            )
        lines.append((product, quantity))

    subtotal = sum(product.unit_price_paise * quantity for product, quantity in lines)
    discount = 5000 if subtotal >= 80000 else 0
    total = subtotal - discount + int(option["fee_paise"])
    canonical = {
        "user_id": str(principal.user_id),
        "merchant_id": str(principal.merchant_id),
        "agent_id": str(principal.agent_id),
        "grant_id": str(principal.grant_id),
        "items": [
            {"sku": p.sku, "qty": q, "unit_price_paise": p.unit_price_paise} for p, q in lines
        ],
        "delivery_option_id": payload.delivery_option_id,
        "subtotal_paise": subtotal,
        "discount_paise": discount,
        "delivery_fee_paise": option["fee_paise"],
        "total_paise": total,
    }
    quote = Quote(
        user_id=principal.user_id,
        merchant_id=principal.merchant_id,
        agent_id=principal.agent_id,
        grant_id=principal.grant_id,
        status=QuoteStatus.OPEN,
        subtotal_paise=subtotal,
        discount_paise=discount,
        delivery_fee_paise=option["fee_paise"],
        tax_paise=0,
        total_paise=total,
        delivery_option_id=payload.delivery_option_id,
        catalog_version=max(p.catalog_version for p, _ in lines),
        inventory_versions={str(p.id): inventories[p.id].version for p, _ in lines},
        canonical_hash=sha256_hex(canonical_json(canonical)),
        expires_at=utcnow() + timedelta(seconds=settings.quote_ttl_seconds),
    )
    quote.items = [
        QuoteItem(
            product_id=product.id,
            sku=product.sku,
            product_name=product.name,
            category=product.category,
            quantity=quantity,
            unit_price_paise=product.unit_price_paise,
            line_total_paise=product.unit_price_paise * quantity,
        )
        for product, quantity in lines
    ]
    session.add(quote)
    session.flush()
    grant = session.get(DelegationGrant, principal.grant_id)
    assert grant is not None
    return quote_out(quote, grant.cumulative_limit_paise - grant.spent_paise - grant.held_paise)


def quote_out(quote: Quote, remaining: int) -> QuoteOut:
    return QuoteOut(
        id=quote.id,
        status=quote.status,
        currency=quote.currency,
        subtotal_paise=quote.subtotal_paise,
        discount_paise=quote.discount_paise,
        delivery_fee_paise=quote.delivery_fee_paise,
        tax_paise=quote.tax_paise,
        total_paise=quote.total_paise,
        delivery_option_id=quote.delivery_option_id,
        expires_at=quote.expires_at,
        canonical_hash=quote.canonical_hash,
        items=[QuoteItemOut.model_validate(item) for item in quote.items],
        remaining_grant_paise=remaining,
    )


def create_checkout(
    session: Session,
    principal: AgentPrincipal,
    payload: CheckoutCreate,
    idempotency_key: str,
) -> Checkout:
    request_hash = semantic_checkout_hash(principal, payload)
    quote_snapshot = session.get(Quote, payload.quote_id)
    if quote_snapshot is None:
        raise TrustCartError("QUOTE_NOT_FOUND", "Quote was not found", 404)
    now = utcnow()
    checkout_id = uuid.uuid4()
    receipt = f"tc_{checkout_id.hex}"
    inserted_id = session.execute(
        pg_insert(Checkout)
        .values(
            id=checkout_id,
            user_id=principal.user_id,
            merchant_id=principal.merchant_id,
            agent_id=principal.agent_id,
            grant_id=principal.grant_id,
            quote_id=payload.quote_id,
            operation="checkout.create.v1",
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            payment_mode=payload.payment_mode.value,
            status=CheckoutStatus.CANCEL_WINDOW.value,
            currency="INR",
            amount_paise=quote_snapshot.total_paise,
            receipt=receipt,
            execute_after=now + timedelta(seconds=settings.cancel_window_seconds),
            payment_deadline_at=now + timedelta(seconds=settings.payment_deadline_seconds),
            late_capture_grace_until=now
            + timedelta(
                seconds=settings.payment_deadline_seconds + settings.late_capture_grace_seconds
            ),
            version=1,
        )
        .on_conflict_do_nothing()
        .returning(Checkout.id)
    ).scalar_one_or_none()
    if inserted_id is None:
        existing = session.scalar(
            select(Checkout).where(
                Checkout.merchant_id == principal.merchant_id,
                Checkout.agent_id == principal.agent_id,
                Checkout.operation == "checkout.create.v1",
                Checkout.idempotency_key == idempotency_key,
            )
        )
        if existing is None:
            consumed = session.scalar(select(Checkout).where(Checkout.quote_id == payload.quote_id))
            if consumed is not None:
                raise ConflictError("QUOTE_ALREADY_CONSUMED", "Quote already has a Checkout", 409)
            raise ConflictError(
                "CHECKOUT_CONFLICT", "Checkout conflicted with an existing operation", 409
            )
        if existing.request_hash != request_hash:
            safe_details = None
            if existing.grant_id == principal.grant_id:
                safe_details = {
                    "checkout_id": str(existing.id),
                    "status": existing.status,
                    "recovery_url": f"/v1/checkouts/{existing.id}",
                }
            raise ConflictError(
                "IDEMPOTENCY_KEY_REUSED",
                "Idempotency key belongs to a different semantic purchase",
                409,
                safe_details,
            )
        return existing

    checkout = session.get(Checkout, inserted_id)
    assert checkout is not None
    quote = session.scalar(
        select(Quote)
        .options(selectinload(Quote.items))
        .where(Quote.id == payload.quote_id)
        .with_for_update()
    )
    if quote is None or (quote.user_id, quote.agent_id, quote.grant_id) != (
        principal.user_id,
        principal.agent_id,
        principal.grant_id,
    ):
        raise AuthorizationError(
            "QUOTE_BINDING_INVALID", "Quote is not bound to this authority", 403
        )
    if quote.status != QuoteStatus.OPEN:
        raise ConflictError("QUOTE_ALREADY_CONSUMED", "Quote is no longer open", 409)
    if quote.expires_at <= now:
        quote.status = QuoteStatus.EXPIRED
        raise ConflictError("QUOTE_EXPIRED", "Quote has expired", 409)
    if any(item.category not in principal.allowed_categories for item in quote.items):
        raise AuthorizationError("CATEGORY_NOT_ALLOWED", "Quote is outside the grant scope", 403)

    reserved = session.execute(
        update(DelegationGrant)
        .where(
            DelegationGrant.id == principal.grant_id,
            DelegationGrant.status == "ACTIVE",
            DelegationGrant.valid_from <= now,
            DelegationGrant.expires_at > now,
            quote.total_paise <= DelegationGrant.per_order_limit_paise,
            DelegationGrant.spent_paise + DelegationGrant.held_paise + quote.total_paise
            <= DelegationGrant.cumulative_limit_paise,
        )
        .values(
            held_paise=DelegationGrant.held_paise + quote.total_paise,
            version=DelegationGrant.version + 1,
        )
        .returning(DelegationGrant.id)
    ).scalar_one_or_none()
    if reserved is None:
        raise AuthorizationError(
            "ALLOWANCE_EXCEEDED", "Grant allowance cannot cover this checkout", 403
        )

    product_ids = sorted((item.product_id for item in quote.items), key=str)
    inventory = {
        row.product_id: row
        for row in session.scalars(
            select(Inventory)
            .where(Inventory.product_id.in_(product_ids))
            .order_by(Inventory.product_id)
            .with_for_update()
        )
    }
    for item in sorted(quote.items, key=lambda i: str(i.product_id)):
        row = inventory.get(item.product_id)
        if row is None or row.on_hand_qty - row.reserved_qty < item.quantity:
            raise ConflictError("INSUFFICIENT_INVENTORY", f"Inventory changed for {item.sku}", 409)
        row.reserved_qty += item.quantity
        row.version += 1
        session.add(
            InventoryReservation(
                checkout_id=checkout.id, product_id=item.product_id, quantity=item.quantity
            )
        )
    session.add(
        AllowanceReservation(
            checkout_id=checkout.id, grant_id=principal.grant_id, amount_paise=quote.total_paise
        )
    )
    quote.status = QuoteStatus.CONSUMED
    append_audit(
        session,
        AuditFact(
            checkout_id=checkout.id,
            aggregate_type="checkout",
            aggregate_id=checkout.id,
            layer="authorization",
            actor=f"agent:{principal.agent_id}",
            action="checkout.reserved",
            reason_code="GRANT_AND_INVENTORY_RESERVED",
            explanation="Deterministic policy reserved allowance and inventory within the approved grant.",
            input_digest=request_hash,
            amount_delta_paise=quote.total_paise,
            data={"quote_id": str(quote.id), "payment_mode": payload.payment_mode.value},
        ),
    )
    return checkout


def checkout_out(checkout: Checkout, razorpay_order_id: str | None = None) -> CheckoutOut:
    return CheckoutOut(
        id=checkout.id,
        quote_id=checkout.quote_id,
        status=checkout.status,
        payment_mode=checkout.payment_mode,
        amount_paise=checkout.amount_paise,
        currency=checkout.currency,
        receipt=checkout.receipt,
        execute_after=checkout.execute_after,
        payment_deadline_at=checkout.payment_deadline_at,
        version=checkout.version,
        razorpay_order_id=razorpay_order_id,
        test_fixture_applied=checkout.test_fixture_applied,
        recovery_url=f"/v1/checkouts/{checkout.id}",
    )


def release_reservations(
    session: Session, checkout: Checkout, terminal_status: CheckoutStatus, reason: str
) -> bool:
    allowance = session.scalar(
        select(AllowanceReservation)
        .where(AllowanceReservation.checkout_id == checkout.id)
        .with_for_update()
    )
    if allowance is None or allowance.status != ReservationStatus.HELD:
        return False
    grant = session.get(DelegationGrant, allowance.grant_id)
    assert grant is not None
    grant.held_paise -= allowance.amount_paise
    grant.version += 1
    allowance.status = ReservationStatus.RELEASED
    allowance.released_reason = reason
    allowance.settled_at = utcnow()
    reservations = session.scalars(
        select(InventoryReservation)
        .where(InventoryReservation.checkout_id == checkout.id)
        .order_by(InventoryReservation.product_id)
        .with_for_update()
    ).all()
    rows = {
        row.product_id: row
        for row in session.scalars(
            select(Inventory)
            .where(Inventory.product_id.in_([r.product_id for r in reservations]))
            .order_by(Inventory.product_id)
            .with_for_update()
        )
    }
    for reservation in reservations:
        if reservation.status == ReservationStatus.HELD:
            rows[reservation.product_id].reserved_qty -= reservation.quantity
            rows[reservation.product_id].version += 1
            reservation.status = ReservationStatus.RELEASED
            reservation.settled_at = utcnow()
    checkout.status = terminal_status
    checkout.version += 1
    append_audit(
        session,
        AuditFact(
            aggregate_type="checkout",
            aggregate_id=checkout.id,
            checkout_id=checkout.id,
            layer="recovery",
            actor="agentauth-worker",
            action="reservations.released",
            reason_code=reason,
            explanation="Provider-confirmed terminal outcome released held resources exactly once.",
            amount_delta_paise=-allowance.amount_paise,
        ),
    )
    return True


def settle_reservations(
    session: Session, checkout: Checkout, terminal_status: CheckoutStatus
) -> bool:
    allowance = session.scalar(
        select(AllowanceReservation)
        .where(AllowanceReservation.checkout_id == checkout.id)
        .with_for_update()
    )
    if allowance is None or allowance.status != ReservationStatus.HELD:
        return False
    grant = session.get(DelegationGrant, allowance.grant_id)
    assert grant is not None
    grant.held_paise -= allowance.amount_paise
    grant.spent_paise += allowance.amount_paise
    grant.version += 1
    allowance.status = ReservationStatus.CONSUMED
    allowance.settled_at = utcnow()
    reservations = session.scalars(
        select(InventoryReservation)
        .where(InventoryReservation.checkout_id == checkout.id)
        .order_by(InventoryReservation.product_id)
        .with_for_update()
    ).all()
    rows = {
        row.product_id: row
        for row in session.scalars(
            select(Inventory)
            .where(Inventory.product_id.in_([r.product_id for r in reservations]))
            .order_by(Inventory.product_id)
            .with_for_update()
        )
    }
    for reservation in reservations:
        if reservation.status == ReservationStatus.HELD:
            row = rows[reservation.product_id]
            row.on_hand_qty -= reservation.quantity
            row.reserved_qty -= reservation.quantity
            row.version += 1
            reservation.status = ReservationStatus.CONSUMED
            reservation.settled_at = utcnow()
    checkout.status = terminal_status
    checkout.version += 1
    append_audit(
        session,
        AuditFact(
            aggregate_type="checkout",
            aggregate_id=checkout.id,
            checkout_id=checkout.id,
            layer="execution",
            actor="agentauth-worker",
            action="reservations.consumed",
            reason_code=str(terminal_status),
            explanation="A confirmed settlement converted held allowance to spent allowance and consumed inventory.",
            amount_delta_paise=allowance.amount_paise,
        ),
    )
    return True
