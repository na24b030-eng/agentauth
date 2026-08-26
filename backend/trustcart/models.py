from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class User(TimestampMixin, Base):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = uuid_pk()
    email: Mapped[str] = mapped_column(String(320), unique=True)
    display_name: Mapped[str] = mapped_column(String(120))
    passcode_hash: Mapped[str] = mapped_column(Text)
    default_postcode: Mapped[str] = mapped_column(String(6), default="560001")
    usual_basket: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)


class Merchant(TimestampMixin, Base):
    __tablename__ = "merchants"
    id: Mapped[uuid.UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(String(120), unique=True)
    currency: Mapped[str] = mapped_column(String(3), default="INR")


class RegisteredAgent(TimestampMixin, Base):
    __tablename__ = "registered_agents"
    __table_args__ = (
        UniqueConstraint("jwk_thumbprint", "key_version", name="uq_agent_thumbprint_version"),
        CheckConstraint("status IN ('ACTIVE', 'REVOKED')", name="ck_agent_status"),
    )
    id: Mapped[uuid.UUID] = uuid_pk()
    merchant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("merchants.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    public_jwk: Mapped[dict[str, Any]] = mapped_column(JSON)
    jwk_thumbprint: Mapped[str] = mapped_column(String(128), index=True)
    key_version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(24), default="ACTIVE")
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class GrantRequest(TimestampMixin, Base):
    __tablename__ = "grant_requests"
    __table_args__ = (
        CheckConstraint("per_order_limit_paise >= 0", name="ck_grant_request_order_limit"),
        CheckConstraint(
            "cumulative_limit_paise >= per_order_limit_paise", name="ck_grant_request_caps"
        ),
        CheckConstraint(
            "status IN ('PENDING', 'APPROVED', 'REJECTED')",
            name="ck_grant_request_status",
        ),
    )
    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    merchant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("merchants.id"), index=True)
    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("registered_agents.id"), index=True)
    allowed_categories: Mapped[list[str]] = mapped_column(JSON)
    per_order_limit_paise: Mapped[int] = mapped_column(BigInteger)
    cumulative_limit_paise: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    auto_execute: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(24), default="PENDING")
    request_digest: Mapped[str] = mapped_column(String(64), unique=True)


class DelegationGrant(TimestampMixin, Base):
    __tablename__ = "delegation_grants"
    __table_args__ = (
        CheckConstraint("per_order_limit_paise >= 0", name="ck_grant_order_limit"),
        CheckConstraint("cumulative_limit_paise >= per_order_limit_paise", name="ck_grant_caps"),
        CheckConstraint("held_paise >= 0", name="ck_grant_held_nonnegative"),
        CheckConstraint("spent_paise >= 0", name="ck_grant_spent_nonnegative"),
        CheckConstraint(
            "held_paise + spent_paise <= cumulative_limit_paise", name="ck_grant_cumulative"
        ),
        CheckConstraint("status IN ('ACTIVE', 'REVOKED', 'EXPIRED')", name="ck_grant_status"),
    )
    id: Mapped[uuid.UUID] = uuid_pk()
    grant_request_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("grant_requests.id"), unique=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    merchant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("merchants.id"), index=True)
    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("registered_agents.id"), index=True)
    agent_key_thumbprint: Mapped[str] = mapped_column(String(128))
    allowed_categories: Mapped[list[str]] = mapped_column(JSON)
    per_order_limit_paise: Mapped[int] = mapped_column(BigInteger)
    cumulative_limit_paise: Mapped[int] = mapped_column(BigInteger)
    held_paise: Mapped[int] = mapped_column(BigInteger, default=0)
    spent_paise: Mapped[int] = mapped_column(BigInteger, default=0)
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    auto_execute: Mapped[bool] = mapped_column(Boolean, default=False)
    immutable_digest: Mapped[str] = mapped_column(String(64), unique=True)
    status: Mapped[str] = mapped_column(String(24), default="ACTIVE", index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ProofNonce(Base):
    __tablename__ = "proof_nonces"
    agent_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("registered_agents.id"), primary_key=True
    )
    nonce: Mapped[str] = mapped_column(String(128), primary_key=True)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class DemoFault(TimestampMixin, Base):
    __tablename__ = "demo_faults"
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    armed: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    armed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    armed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Product(TimestampMixin, Base):
    __tablename__ = "products"
    __table_args__ = (UniqueConstraint("merchant_id", "sku", name="uq_product_merchant_sku"),)
    id: Mapped[uuid.UUID] = uuid_pk()
    merchant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("merchants.id"), index=True)
    sku: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(180))
    description: Mapped[str] = mapped_column(Text, default="")
    category: Mapped[str] = mapped_column(String(64), index=True)
    unit_price_paise: Mapped[int] = mapped_column(BigInteger)
    tax_inclusive: Mapped[bool] = mapped_column(Boolean, default=True)
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    catalog_version: Mapped[int] = mapped_column(Integer, default=1)
    __table_args__ += (CheckConstraint("unit_price_paise >= 0", name="ck_product_price"),)


class Inventory(TimestampMixin, Base):
    __tablename__ = "inventory"
    __table_args__ = (
        CheckConstraint("on_hand_qty >= 0", name="ck_inventory_on_hand"),
        CheckConstraint("reserved_qty >= 0", name="ck_inventory_reserved"),
        CheckConstraint("reserved_qty <= on_hand_qty", name="ck_inventory_available"),
    )
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id"), primary_key=True)
    on_hand_qty: Mapped[int] = mapped_column(Integer)
    reserved_qty: Mapped[int] = mapped_column(Integer, default=0)
    version: Mapped[int] = mapped_column(Integer, default=1)
    product: Mapped[Product] = relationship()


class Quote(TimestampMixin, Base):
    __tablename__ = "quotes"
    __table_args__ = (
        CheckConstraint("total_paise >= 0", name="ck_quote_total"),
        CheckConstraint("status IN ('OPEN', 'CONSUMED', 'EXPIRED')", name="ck_quote_status"),
        Index("ix_quotes_binding", "user_id", "agent_id", "grant_id"),
    )
    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    merchant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("merchants.id"))
    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("registered_agents.id"))
    grant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("delegation_grants.id"))
    status: Mapped[str] = mapped_column(String(24), default="OPEN", index=True)
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    subtotal_paise: Mapped[int] = mapped_column(BigInteger)
    discount_paise: Mapped[int] = mapped_column(BigInteger, default=0)
    delivery_fee_paise: Mapped[int] = mapped_column(BigInteger, default=0)
    tax_paise: Mapped[int] = mapped_column(BigInteger, default=0)
    total_paise: Mapped[int] = mapped_column(BigInteger)
    delivery_option_id: Mapped[str] = mapped_column(String(64))
    catalog_version: Mapped[int] = mapped_column(Integer)
    inventory_versions: Mapped[dict[str, int]] = mapped_column(JSON)
    canonical_hash: Mapped[str] = mapped_column(String(64), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    items: Mapped[list[QuoteItem]] = relationship(
        back_populates="quote", cascade="all, delete-orphan"
    )


class QuoteItem(Base):
    __tablename__ = "quote_items"
    __table_args__ = (CheckConstraint("quantity > 0", name="ck_quote_item_quantity"),)
    id: Mapped[uuid.UUID] = uuid_pk()
    quote_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("quotes.id", ondelete="CASCADE"))
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id"))
    sku: Mapped[str] = mapped_column(String(64))
    product_name: Mapped[str] = mapped_column(String(180))
    category: Mapped[str] = mapped_column(String(64))
    quantity: Mapped[int] = mapped_column(Integer)
    unit_price_paise: Mapped[int] = mapped_column(BigInteger)
    line_total_paise: Mapped[int] = mapped_column(BigInteger)
    quote: Mapped[Quote] = relationship(back_populates="items")


class Checkout(TimestampMixin, Base):
    __tablename__ = "checkouts"
    __table_args__ = (
        UniqueConstraint(
            "merchant_id",
            "agent_id",
            "operation",
            "idempotency_key",
            name="uq_checkout_idempotency",
        ),
        CheckConstraint("amount_paise >= 0", name="ck_checkout_amount"),
        CheckConstraint(
            "status IN ('CANCEL_WINDOW', 'ORDER_CREATING', 'PAYMENT_PENDING', "
            "'EXPIRING', 'RECONCILING', 'PAID', 'SIMULATED_SETTLED', 'CANCELLED', "
            "'EXPIRED', 'FAILED_TERMINAL', 'LATE_CAPTURE_INCIDENT')",
            name="ck_checkout_status",
        ),
        CheckConstraint(
            "payment_mode IN ('RAZORPAY_PAYMENT_LAB', 'DELEGATED_DEBIT_SIMULATOR')",
            name="ck_checkout_payment_mode",
        ),
        Index("ix_checkouts_worker", "status", "next_retry_at", "execute_after"),
    )
    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    merchant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("merchants.id"), index=True)
    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("registered_agents.id"), index=True)
    grant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("delegation_grants.id"), index=True)
    quote_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("quotes.id"), unique=True)
    operation: Mapped[str] = mapped_column(String(64), default="checkout.create.v1")
    idempotency_key: Mapped[str] = mapped_column(String(128))
    request_hash: Mapped[str] = mapped_column(String(64))
    payment_mode: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(40), index=True)
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    amount_paise: Mapped[int] = mapped_column(BigInteger)
    receipt: Mapped[str] = mapped_column(String(40), unique=True)
    execute_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    payment_deadline_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    late_capture_grace_until: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    reconciliation_token: Mapped[str | None] = mapped_column(String(64))
    reconciliation_version: Mapped[int | None] = mapped_column(Integer)
    provider_observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    last_error_code: Mapped[str | None] = mapped_column(String(80))
    last_error_detail: Mapped[str | None] = mapped_column(Text)
    test_fixture_applied: Mapped[bool] = mapped_column(Boolean, default=False)
    version: Mapped[int] = mapped_column(Integer, default=1)
    quote: Mapped[Quote] = relationship()


class AllowanceReservation(TimestampMixin, Base):
    __tablename__ = "allowance_reservations"
    __table_args__ = (
        CheckConstraint("amount_paise > 0", name="ck_allowance_amount"),
        CheckConstraint("status IN ('HELD', 'CONSUMED', 'RELEASED')", name="ck_allowance_status"),
    )
    id: Mapped[uuid.UUID] = uuid_pk()
    checkout_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("checkouts.id"), unique=True)
    grant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("delegation_grants.id"), index=True)
    amount_paise: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(24), default="HELD")
    released_reason: Mapped[str | None] = mapped_column(String(120))
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class InventoryReservation(TimestampMixin, Base):
    __tablename__ = "inventory_reservations"
    __table_args__ = (
        UniqueConstraint("checkout_id", "product_id", name="uq_inventory_reservation"),
        CheckConstraint("quantity > 0", name="ck_inventory_reservation_quantity"),
        CheckConstraint(
            "status IN ('HELD', 'CONSUMED', 'RELEASED')",
            name="ck_inventory_reservation_status",
        ),
    )
    id: Mapped[uuid.UUID] = uuid_pk()
    checkout_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("checkouts.id"), index=True)
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id"))
    quantity: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(24), default="HELD")
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RazorpayOrder(TimestampMixin, Base):
    __tablename__ = "razorpay_orders"
    id: Mapped[uuid.UUID] = uuid_pk()
    checkout_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("checkouts.id"), unique=True)
    razorpay_order_id: Mapped[str | None] = mapped_column(String(64), unique=True)
    receipt: Mapped[str] = mapped_column(String(40), unique=True)
    status: Mapped[str] = mapped_column(String(32), default="CREATING")
    amount_paise: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_reconciled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    response_digest: Mapped[str | None] = mapped_column(String(64))


class PaymentAttempt(TimestampMixin, Base):
    __tablename__ = "payment_attempts"
    id: Mapped[uuid.UUID] = uuid_pk()
    checkout_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("checkouts.id"), index=True)
    razorpay_order_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("razorpay_orders.id"))
    provider_payment_id: Mapped[str] = mapped_column(String(64), unique=True)
    provider_status: Mapped[str] = mapped_column(String(32))
    amount_paise: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    captured: Mapped[bool] = mapped_column(Boolean, default=False)
    error_code: Mapped[str | None] = mapped_column(String(100))
    error_description: Mapped[str | None] = mapped_column(Text)
    provider_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WebhookEvent(Base):
    __tablename__ = "webhook_events"
    __table_args__ = (UniqueConstraint("provider", "provider_event_id", name="uq_webhook_event"),)
    id: Mapped[uuid.UUID] = uuid_pk()
    provider: Mapped[str] = mapped_column(String(32), default="razorpay")
    provider_event_id: Mapped[str] = mapped_column(String(128))
    event_type: Mapped[str] = mapped_column(String(80), index=True)
    payload_digest: Mapped[str] = mapped_column(String(64))
    sanitized_payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(24), default="RECEIVED")
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_detail: Mapped[str | None] = mapped_column(Text)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (
        UniqueConstraint("checkout_id", "sequence", name="uq_audit_checkout_sequence"),
        Index("ix_audit_aggregate", "aggregate_type", "aggregate_id"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    checkout_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("checkouts.id"), index=True)
    aggregate_type: Mapped[str] = mapped_column(String(64))
    aggregate_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True))
    sequence: Mapped[int] = mapped_column(Integer)
    layer: Mapped[str] = mapped_column(String(32))
    actor: Mapped[str] = mapped_column(String(120))
    action: Mapped[str] = mapped_column(String(120))
    reason_code: Mapped[str] = mapped_column(String(100))
    explanation: Mapped[str] = mapped_column(Text)
    input_digest: Mapped[str | None] = mapped_column(String(64))
    amount_delta_paise: Mapped[int] = mapped_column(BigInteger, default=0)
    data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    trace_id: Mapped[str | None] = mapped_column(String(100))
    correlation_id: Mapped[str | None] = mapped_column(String(100))
    previous_hash: Mapped[str | None] = mapped_column(String(64))
    event_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AgentRun(TimestampMixin, Base):
    __tablename__ = "agent_runs"
    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("registered_agents.id"))
    grant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("delegation_grants.id"))
    user_message: Mapped[str] = mapped_column(Text)
    payment_mode: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(40), default="QUEUED")
    active_quote_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("quotes.id"))
    checkout_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("checkouts.id"))
    final_response: Mapped[str | None] = mapped_column(Text)
    model_name: Mapped[str] = mapped_column(String(100))
    thinking_level: Mapped[str] = mapped_column(String(24))
    tool_call_count: Mapped[int] = mapped_column(Integer, default=0)
    turn_count: Mapped[int] = mapped_column(Integer, default=0)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(100))
    error_detail: Mapped[str | None] = mapped_column(Text)


class AgentToolEvent(Base):
    __tablename__ = "agent_tool_events"
    __table_args__ = (UniqueConstraint("run_id", "sequence", name="uq_agent_tool_sequence"),)
    id: Mapped[uuid.UUID] = uuid_pk()
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agent_runs.id", ondelete="CASCADE"))
    sequence: Mapped[int] = mapped_column(Integer)
    tool_name: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(32))
    input_digest: Mapped[str | None] = mapped_column(String(64))
    output_digest: Mapped[str | None] = mapped_column(String(64))
    summary: Mapped[str] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
