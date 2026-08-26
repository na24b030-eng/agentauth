from __future__ import annotations

import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from trustcart.commerce import (
    create_checkout,
    create_quote,
    release_reservations,
    settle_reservations,
)
from trustcart.crypto import generate_p256_private_key, jwk_thumbprint, public_key_to_jwk
from trustcart.db import Base
from trustcart.enums import CheckoutStatus, ReservationStatus, WebhookStatus
from trustcart.errors import TrustCartError
from trustcart.models import (
    AllowanceReservation,
    Checkout,
    DelegationGrant,
    GrantRequest,
    Inventory,
    Merchant,
    PaymentAttempt,
    Product,
    RazorpayOrder,
    RegisteredAgent,
    User,
    WebhookEvent,
)
from trustcart.payments import process_webhook
from trustcart.pop import AgentPrincipal
from trustcart.schemas import CheckoutCreate, QuoteCreate, QuoteItemInput

pytestmark = pytest.mark.postgres


@pytest.fixture()
def pg_sessions():
    url = os.getenv("TEST_DATABASE_URL")
    disposable = os.getenv("TRUSTCART_TEST_DATABASE_IS_DISPOSABLE") == "yes"
    if not url or not disposable:
        pytest.skip("requires TEST_DATABASE_URL and TRUSTCART_TEST_DATABASE_IS_DISPOSABLE=yes")
    engine = create_engine(url, pool_size=8)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield sessionmaker(engine, expire_on_commit=False)
    Base.metadata.drop_all(engine)
    engine.dispose()


def seed_authority(
    sessions,
    *,
    per_order_limit_paise: int = 11_900,
    cumulative_limit_paise: int = 11_900,
    on_hand_qty: int = 10,
):
    now = datetime.now(UTC)
    with sessions.begin() as session:
        merchant = Merchant(name=f"merchant-{uuid.uuid4()}")
        user = User(
            email=f"{uuid.uuid4()}@example.test",
            display_name="Test Buyer",
            passcode_hash="not-used",
        )
        session.add_all([merchant, user])
        session.flush()
        jwk = public_key_to_jwk(generate_p256_private_key().public_key())
        agent = RegisteredAgent(
            merchant_id=merchant.id,
            name="Concurrency Agent",
            public_jwk=jwk,
            jwk_thumbprint=jwk_thumbprint(jwk),
        )
        session.add(agent)
        session.flush()
        request = GrantRequest(
            user_id=user.id,
            merchant_id=merchant.id,
            agent_id=agent.id,
            allowed_categories=["dairy"],
            per_order_limit_paise=per_order_limit_paise,
            cumulative_limit_paise=cumulative_limit_paise,
            valid_from=now - timedelta(minutes=1),
            expires_at=now + timedelta(hours=1),
            auto_execute=True,
            request_digest=uuid.uuid4().hex,
        )
        session.add(request)
        session.flush()
        grant = DelegationGrant(
            grant_request_id=request.id,
            user_id=user.id,
            merchant_id=merchant.id,
            agent_id=agent.id,
            agent_key_thumbprint=agent.jwk_thumbprint,
            allowed_categories=["dairy"],
            per_order_limit_paise=per_order_limit_paise,
            cumulative_limit_paise=cumulative_limit_paise,
            valid_from=now - timedelta(minutes=1),
            expires_at=now + timedelta(hours=1),
            auto_execute=True,
            immutable_digest=uuid.uuid4().hex,
        )
        product = Product(
            merchant_id=merchant.id,
            sku="MILK-TEST",
            name="Test Milk",
            category="dairy",
            unit_price_paise=7_000,
        )
        session.add_all([grant, product])
        session.flush()
        session.add(Inventory(product_id=product.id, on_hand_qty=on_hand_qty))
        principal = AgentPrincipal(
            agent.id,
            grant.id,
            user.id,
            merchant.id,
            grant.immutable_digest,
            ("dairy",),
            True,
        )
    return principal


def test_concurrent_checkouts_cannot_exceed_shared_cap(pg_sessions) -> None:
    principal = seed_authority(pg_sessions)
    with pg_sessions.begin() as session:
        quote_ids = [
            create_quote(
                session,
                principal,
                QuoteCreate(
                    items=[QuoteItemInput(sku="MILK-TEST", quantity=1)],
                    delivery_option_id="tonight",
                ),
            ).id
            for _ in range(2)
        ]
    barrier = threading.Barrier(2)

    def attempt(quote_id: uuid.UUID) -> str:
        try:
            with pg_sessions.begin() as session:
                barrier.wait(timeout=5)
                create_checkout(
                    session,
                    principal,
                    CheckoutCreate(
                        quote_id=quote_id,
                        payment_mode="DELEGATED_DEBIT_SIMULATOR",
                    ),
                    f"idem-{uuid.uuid4().hex}",
                )
            return "created"
        except TrustCartError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, quote_ids))
    assert sorted(results) == ["ALLOWANCE_EXCEEDED", "created"]

    with pg_sessions() as session:
        grant = session.get(DelegationGrant, principal.grant_id)
        inventory = session.scalar(select(Inventory))
        checkout_count = session.scalar(select(func.count()).select_from(Checkout))
        assert grant is not None and grant.held_paise == 11_900
        assert grant.spent_paise + grant.held_paise <= grant.cumulative_limit_paise
        assert inventory is not None and inventory.reserved_qty == 1
        assert checkout_count == 1


def test_concurrent_checkouts_cannot_over_reserve_inventory(pg_sessions) -> None:
    principal = seed_authority(
        pg_sessions,
        per_order_limit_paise=20_000,
        cumulative_limit_paise=40_000,
        on_hand_qty=1,
    )
    with pg_sessions.begin() as session:
        quote_ids = [
            create_quote(
                session,
                principal,
                QuoteCreate(
                    items=[QuoteItemInput(sku="MILK-TEST", quantity=1)],
                    delivery_option_id="tonight",
                ),
            ).id
            for _ in range(2)
        ]
    barrier = threading.Barrier(2)

    def attempt(quote_id: uuid.UUID) -> str:
        try:
            with pg_sessions.begin() as session:
                barrier.wait(timeout=5)
                create_checkout(
                    session,
                    principal,
                    CheckoutCreate(
                        quote_id=quote_id,
                        payment_mode="DELEGATED_DEBIT_SIMULATOR",
                    ),
                    f"idem-{uuid.uuid4().hex}",
                )
            return "created"
        except TrustCartError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, quote_ids))
    assert sorted(results) == ["INSUFFICIENT_INVENTORY", "created"]

    with pg_sessions() as session:
        grant = session.get(DelegationGrant, principal.grant_id)
        inventory = session.scalar(select(Inventory))
        assert grant is not None and grant.held_paise == 11_900
        assert inventory is not None and inventory.reserved_qty == 1


def test_checkout_idempotency_and_consumed_quote_are_database_enforced(pg_sessions) -> None:
    principal = seed_authority(pg_sessions)
    with pg_sessions.begin() as session:
        quote_id = create_quote(
            session,
            principal,
            QuoteCreate(
                items=[QuoteItemInput(sku="MILK-TEST", quantity=1)],
                delivery_option_id="tonight",
            ),
        ).id
        payload = CheckoutCreate(
            quote_id=quote_id,
            payment_mode="DELEGATED_DEBIT_SIMULATOR",
        )
        first = create_checkout(session, principal, payload, "stable-key")
        retried = create_checkout(session, principal, payload, "stable-key")
        assert retried.id == first.id

    with pytest.raises(TrustCartError) as raised, pg_sessions.begin() as session:
        create_checkout(session, principal, payload, "deliberately-new-key")
    assert raised.value.code == "QUOTE_ALREADY_CONSUMED"

    with pg_sessions() as session:
        grant = session.get(DelegationGrant, principal.grant_id)
        assert session.scalar(select(func.count()).select_from(Checkout)) == 1
        assert grant is not None and grant.held_paise == 11_900


@pytest.mark.parametrize("terminal", ["settled", "released"])
def test_reservation_terminal_effect_is_exactly_once(pg_sessions, terminal: str) -> None:
    principal = seed_authority(pg_sessions)
    with pg_sessions.begin() as session:
        quote_id = create_quote(
            session,
            principal,
            QuoteCreate(
                items=[QuoteItemInput(sku="MILK-TEST", quantity=1)],
                delivery_option_id="tonight",
            ),
        ).id
        checkout = create_checkout(
            session,
            principal,
            CheckoutCreate(
                quote_id=quote_id,
                payment_mode="DELEGATED_DEBIT_SIMULATOR",
            ),
            f"terminal-{terminal}",
        )
        checkout_id = checkout.id

    with pg_sessions.begin() as session:
        checkout = session.get(Checkout, checkout_id)
        assert checkout is not None
        if terminal == "settled":
            assert settle_reservations(
                session, checkout, CheckoutStatus.SIMULATED_SETTLED
            ) is True
            assert settle_reservations(
                session, checkout, CheckoutStatus.SIMULATED_SETTLED
            ) is False
        else:
            assert release_reservations(
                session, checkout, CheckoutStatus.CANCELLED, "BUYER_CANCELLED"
            ) is True
            assert release_reservations(
                session, checkout, CheckoutStatus.CANCELLED, "BUYER_CANCELLED"
            ) is False

    with pg_sessions() as session:
        grant = session.get(DelegationGrant, principal.grant_id)
        inventory = session.scalar(select(Inventory))
        allowance = session.scalar(select(AllowanceReservation))
        assert grant is not None and grant.held_paise == 0
        assert inventory is not None and inventory.reserved_qty == 0
        if terminal == "settled":
            assert grant.spent_paise == 11_900
            assert inventory.on_hand_qty == 9
            assert allowance is not None and allowance.status == ReservationStatus.CONSUMED
        else:
            assert grant.spent_paise == 0
            assert inventory.on_hand_qty == 10
            assert allowance is not None and allowance.status == ReservationStatus.RELEASED


def test_failed_attempt_then_capture_settles_once(pg_sessions) -> None:
    principal = seed_authority(pg_sessions)
    with pg_sessions.begin() as session:
        quote_id = create_quote(
            session,
            principal,
            QuoteCreate(
                items=[QuoteItemInput(sku="MILK-TEST", quantity=1)],
                delivery_option_id="tonight",
            ),
        ).id
        checkout = create_checkout(
            session,
            principal,
            CheckoutCreate(
                quote_id=quote_id,
                payment_mode="RAZORPAY_PAYMENT_LAB",
            ),
            "webhook-order",
        )
        checkout.status = CheckoutStatus.PAYMENT_PENDING
        order = RazorpayOrder(
            checkout_id=checkout.id,
            razorpay_order_id="order_test_capture",
            receipt=checkout.receipt,
            status="CREATED",
            amount_paise=checkout.amount_paise,
            currency=checkout.currency,
        )
        session.add(order)
        checkout_id = checkout.id

    def add_event(session, event_id: str, event_type: str, status: str, captured: bool):
        event = WebhookEvent(
            provider="razorpay",
            provider_event_id=event_id,
            event_type=event_type,
            payload_digest=uuid.uuid4().hex,
            sanitized_payload={
                "event": event_type,
                "payment": {
                    "id": "pay_same_attempt",
                    "order_id": "order_test_capture",
                    "amount": 11_900,
                    "currency": "INR",
                    "status": status,
                    "captured": captured,
                },
                "order": {},
            },
            status=WebhookStatus.RECEIVED,
        )
        session.add(event)
        session.flush()
        process_webhook(session, event)

    with pg_sessions.begin() as session:
        add_event(session, "evt-failed", "payment.failed", "failed", False)
    with pg_sessions() as session:
        checkout = session.get(Checkout, checkout_id)
        grant = session.get(DelegationGrant, principal.grant_id)
        assert checkout is not None and checkout.status == CheckoutStatus.PAYMENT_PENDING
        assert grant is not None and grant.held_paise == 11_900 and grant.spent_paise == 0

    with pg_sessions.begin() as session:
        add_event(session, "evt-captured", "payment.captured", "captured", True)
    with pg_sessions.begin() as session:
        add_event(session, "evt-captured-retry", "payment.captured", "captured", True)

    with pg_sessions() as session:
        checkout = session.get(Checkout, checkout_id)
        grant = session.get(DelegationGrant, principal.grant_id)
        inventory = session.scalar(select(Inventory))
        attempt = session.scalar(select(PaymentAttempt))
        assert checkout is not None and checkout.status == CheckoutStatus.PAID
        assert grant is not None and grant.held_paise == 0 and grant.spent_paise == 11_900
        assert inventory is not None and inventory.on_hand_qty == 9 and inventory.reserved_qty == 0
        assert attempt is not None and attempt.captured is True
