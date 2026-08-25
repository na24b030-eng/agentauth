from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .enums import PaymentMode


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)


class LoginRequest(StrictModel):
    email: str
    passcode: str


class LoginResponse(StrictModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in_seconds: int = 1800
    demo_identity: bool = True
    user_id: uuid.UUID
    display_name: str


class AgentCreate(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    public_jwk: dict[str, str]
    key_version: int = Field(default=1, ge=1)


class AgentOut(StrictModel):
    id: uuid.UUID
    name: str
    jwk_thumbprint: str
    key_version: int
    status: str


class AgentIdentityOut(StrictModel):
    id: uuid.UUID
    name: str
    jwk_thumbprint: str
    key_version: int
    status: str


class GrantRequestCreate(StrictModel):
    user_id: uuid.UUID
    allowed_categories: list[str] = Field(min_length=1)
    per_order_limit_paise: int = Field(gt=0)
    cumulative_limit_paise: int = Field(gt=0)
    expires_at: datetime
    auto_execute: bool = False

    @field_validator("allowed_categories")
    @classmethod
    def unique_categories(cls, value: list[str]) -> list[str]:
        return sorted(set(value))


class GrantApproval(StrictModel):
    acknowledge_demo_identity: Literal[True]


class GrantOut(StrictModel):
    id: uuid.UUID
    user_id: uuid.UUID
    agent_id: uuid.UUID
    allowed_categories: list[str]
    per_order_limit_paise: int
    cumulative_limit_paise: int
    held_paise: int
    spent_paise: int
    expires_at: datetime
    auto_execute: bool
    immutable_digest: str
    status: str


class ProductOut(StrictModel):
    id: uuid.UUID
    sku: str
    name: str
    description: str
    category: str
    unit_price_paise: int
    tags: list[str]
    available_quantity: int


class DeliveryOptionRequest(StrictModel):
    postcode: str = Field(pattern=r"^[1-9][0-9]{5}$")


class DeliveryOptionOut(StrictModel):
    id: str
    label: str
    window: str
    fee_paise: int
    cutoff_at: datetime


class QuoteItemInput(StrictModel):
    sku: str = Field(min_length=1, max_length=64)
    quantity: int = Field(ge=1, le=50)


class QuoteCreate(StrictModel):
    items: list[QuoteItemInput] = Field(min_length=1, max_length=30)
    delivery_option_id: str


class QuoteItemOut(StrictModel):
    sku: str
    product_name: str
    category: str
    quantity: int
    unit_price_paise: int
    line_total_paise: int


class QuoteOut(StrictModel):
    id: uuid.UUID
    status: str
    currency: str
    subtotal_paise: int
    discount_paise: int
    delivery_fee_paise: int
    tax_paise: int
    total_paise: int
    delivery_option_id: str
    expires_at: datetime
    canonical_hash: str
    items: list[QuoteItemOut]
    remaining_grant_paise: int


class CheckoutCreate(StrictModel):
    quote_id: uuid.UUID
    payment_mode: PaymentMode


class CheckoutOut(StrictModel):
    id: uuid.UUID
    quote_id: uuid.UUID
    status: str
    payment_mode: str
    amount_paise: int
    currency: str
    receipt: str
    execute_after: datetime
    payment_deadline_at: datetime
    version: int
    razorpay_order_id: str | None = None
    recovery_url: str


class AgentRunCreate(StrictModel):
    message: str = Field(min_length=1, max_length=2000)
    payment_mode: PaymentMode = PaymentMode.DELEGATED_DEBIT_SIMULATOR
    grant_id: uuid.UUID


class AgentRunOut(StrictModel):
    id: uuid.UUID
    status: str
    final_response: str | None
    active_quote_id: uuid.UUID | None
    checkout_id: uuid.UUID | None
    tool_call_count: int
    turn_count: int
    error_code: str | None


class PaymentConfigOut(StrictModel):
    enabled: bool
    key_id: str | None = None
    environment: Literal["test"] = "test"


class DemoFaultUpdate(StrictModel):
    armed: bool


class DemoFaultOut(StrictModel):
    key: str
    armed: bool
    armed_at: datetime | None
    consumed_at: datetime | None


class AuditEventOut(StrictModel):
    id: int
    checkout_id: uuid.UUID | None
    sequence: int
    layer: str
    actor: str
    action: str
    reason_code: str
    explanation: str
    amount_delta_paise: int
    data: dict[str, Any]
    previous_hash: str | None
    event_hash: str
    created_at: datetime


class ErrorResponse(StrictModel):
    code: str
    message: str
    details: dict[str, Any] | None = None
