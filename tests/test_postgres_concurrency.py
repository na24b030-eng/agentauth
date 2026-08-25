from __future__ import annotations

import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from trustcart.commerce import create_checkout, create_quote
from trustcart.crypto import generate_p256_private_key, jwk_thumbprint, public_key_to_jwk
from trustcart.db import Base
from trustcart.errors import TrustCartError
from trustcart.models import (
    Checkout,
    DelegationGrant,
    GrantRequest,
    Inventory,
    Merchant,
    Product,
    RegisteredAgent,
    User,
)
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


def seed_authority(sessions):
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
            per_order_limit_paise=11_900,
            cumulative_limit_paise=11_900,
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
            per_order_limit_paise=11_900,
            cumulative_limit_paise=11_900,
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
        session.add(Inventory(product_id=product.id, on_hand_qty=10))
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
