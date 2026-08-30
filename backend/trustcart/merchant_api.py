from __future__ import annotations

import hashlib
import hmac
import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, FastAPI, Header, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

from .audit import AuditFact, append_audit
from .auth import issue_demo_token, merchant_user, verify_passcode
from .commerce import (
    DELIVERY_OPTIONS,
    checkout_out,
    create_checkout,
    create_quote,
    quote_out,
    release_reservations,
)
from .config import get_settings
from .crypto import canonical_json, jwk_thumbprint, load_private_key, sha256_hex
from .db import get_session
from .demo_faults import set_demo_fault
from .enums import CheckoutStatus, PaymentMode
from .errors import AuthorizationError, TrustCartError
from .models import (
    AgentRun,
    AgentToolEvent,
    AllowanceReservation,
    AuditEvent,
    Checkout,
    DelegationGrant,
    DemoFault,
    GrantRequest,
    Inventory,
    InventoryReservation,
    PaymentAttempt,
    Product,
    ProofNonce,
    Quote,
    QuoteItem,
    RazorpayOrder,
    RegisteredAgent,
    User,
    WebhookEvent,
)
from .payments import persist_webhook, process_webhook
from .pop import (
    AgentPrincipal,
    RegisteredAgentPrincipal,
    require_agent_proof,
    require_registered_agent_proof,
)
from .schemas import (
    AgentCreate,
    AgentIdentityOut,
    AgentOut,
    AuditEventOut,
    CheckoutCreate,
    CheckoutOut,
    DeliveryOptionOut,
    DeliveryOptionRequest,
    DemoFaultOut,
    DemoFaultUpdate,
    GrantApproval,
    GrantOut,
    GrantRequestCreate,
    LoginRequest,
    LoginResponse,
    PaymentConfigOut,
    ProductOut,
    QuoteCreate,
    QuoteOut,
    WebhookFixtureOut,
    WebhookFixtureRequest,
)
from .seed import CATALOG, DEMO_MERCHANT_ID

app = FastAPI(title="AgentAuth Merchant API", version="0.1.0")
settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        origin.strip() for origin in settings.frontend_origin.split(",") if origin.strip()
    ],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(TrustCartError)
async def trustcart_error_handler(_: Request, exc: TrustCartError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"code": exc.code, "message": exc.message, "details": exc.details},
    )


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "service": "merchant-api",
        "razorpay_configured": bool(settings.razorpay_key_id and settings.razorpay_key_secret),
    }


@app.get("/v1/payment-config", response_model=PaymentConfigOut)
def payment_config() -> PaymentConfigOut:
    """Expose only Razorpay's publishable Test Mode key to the browser."""
    key_id = settings.razorpay_key_id.get_secret_value() if settings.razorpay_key_id else None
    secret = (
        settings.razorpay_key_secret.get_secret_value() if settings.razorpay_key_secret else None
    )
    return PaymentConfigOut(enabled=bool(key_id and secret), key_id=key_id)


@app.post("/v1/developer/reset-demo")
def reset_demo_state(
    _: User = Depends(merchant_user), session: Session = Depends(get_session)
) -> dict:
    """Reset only local fictional state; never orphan configured provider objects."""
    if settings.environment == "production":
        raise AuthorizationError(
            "DEVELOPER_FIXTURES_DISABLED",
            "Demo reset is disabled in production environments",
            403,
        )
    if settings.razorpay_key_id or settings.razorpay_key_secret:
        raise TrustCartError(
            "LOCAL_RESET_DISABLED_WITH_PROVIDER",
            "Disable Razorpay Test Mode credentials before deleting local demo state",
            409,
        )
    for model in (
        AgentToolEvent,
        AgentRun,
        AuditEvent,
        WebhookEvent,
        PaymentAttempt,
        RazorpayOrder,
        InventoryReservation,
        AllowanceReservation,
        Checkout,
        QuoteItem,
        Quote,
        ProofNonce,
        DelegationGrant,
        GrantRequest,
        DemoFault,
    ):
        session.execute(delete(model))
    seeded_stock = {sku: stock for sku, _, _, _, _, stock in CATALOG}
    for product, inventory in session.execute(
        select(Product, Inventory)
        .join(Inventory, Inventory.product_id == Product.id)
        .where(Product.merchant_id == DEMO_MERCHANT_ID)
        .with_for_update()
    ):
        inventory.on_hand_qty = seeded_stock.get(product.sku, inventory.on_hand_qty)
        inventory.reserved_qty = 0
        inventory.version += 1
    session.commit()
    return {
        "reset": True,
        "scope": "local_fictional_demo_state",
        "provider_objects_deleted": False,
    }


@app.post("/v1/developer/faults/{fault_key}", response_model=DemoFaultOut)
def update_demo_fault(
    fault_key: str,
    payload: DemoFaultUpdate,
    user: User = Depends(merchant_user),
    session: Session = Depends(get_session),
) -> DemoFaultOut:
    if settings.environment == "production":
        raise AuthorizationError(
            "DEVELOPER_FIXTURES_DISABLED",
            "Failure fixtures are disabled in production environments",
            403,
        )
    row = set_demo_fault(session, fault_key, armed=payload.armed, user_id=user.id)
    session.commit()
    return DemoFaultOut.model_validate(row)


@app.post("/v1/developer/webhook-fixture", response_model=WebhookFixtureOut)
def run_webhook_fixture(
    payload: WebhookFixtureRequest,
    user: User = Depends(merchant_user),
    session: Session = Depends(get_session),
) -> WebhookFixtureOut:
    """Apply a disclosed captured→stale-failed→duplicate-failed Test Mode fixture."""
    if settings.environment == "production":
        raise AuthorizationError(
            "DEVELOPER_FIXTURES_DISABLED",
            "Failure fixtures are disabled in production environments",
            403,
        )
    if not settings.razorpay_webhook_secret:
        raise TrustCartError(
            "WEBHOOK_SECRET_NOT_CONFIGURED",
            "A Razorpay Test Mode webhook secret is required for this fixture",
            503,
        )
    checkout = session.scalar(
        select(Checkout).where(Checkout.id == payload.checkout_id).with_for_update()
    )
    if checkout is None or checkout.user_id != user.id:
        raise TrustCartError("CHECKOUT_NOT_FOUND", "Checkout was not found", 404)
    order = session.scalar(select(RazorpayOrder).where(RazorpayOrder.checkout_id == checkout.id))
    if (
        checkout.payment_mode != PaymentMode.RAZORPAY_PAYMENT_LAB
        or order is None
        or order.razorpay_order_id is None
    ):
        raise TrustCartError(
            "RAZORPAY_ORDER_REQUIRED",
            "The fixture requires a real Test Mode Order bound to this Checkout",
            409,
        )
    if checkout.test_fixture_applied:
        return WebhookFixtureOut(
            checkout=checkout_out(checkout, order.razorpay_order_id),
            created_events=0,
            duplicate_deduplicated=True,
            disclosure="Previously applied signed developer fixture; not a provider payment.",
        )
    if checkout.status not in {
        CheckoutStatus.PAYMENT_PENDING,
        CheckoutStatus.EXPIRING,
        CheckoutStatus.RECONCILING,
    }:
        raise TrustCartError(
            "CHECKOUT_NOT_FIXTURE_ELIGIBLE",
            "The Checkout is not waiting for a Razorpay payment",
            409,
        )

    checkout.test_fixture_applied = True
    append_audit(
        session,
        AuditFact(
            aggregate_type="checkout",
            aggregate_id=checkout.id,
            checkout_id=checkout.id,
            layer="recovery",
            actor=f"developer:{user.id}",
            action="webhook.fixture_started",
            reason_code="DISCLOSED_TEST_WEBHOOK_SEQUENCE",
            explanation="A signed developer fixture will test captured, stale-failed, and duplicate webhook convergence; it is not a provider payment.",
        ),
    )

    captured_payment_id = f"pay_fixture_capture_{checkout.id.hex}"
    failed_payment_id = f"pay_fixture_failed_{checkout.id.hex}"
    failed_id = f"fixture-failed-{checkout.id.hex}"
    captured_id = f"fixture-captured-{checkout.id.hex}"
    order_entity = {
        "id": order.razorpay_order_id,
        "receipt": checkout.receipt,
        "amount": checkout.amount_paise,
        "currency": checkout.currency,
        "status": "paid",
    }

    def fixture_payload(event_type: str, payment_id: str, *, captured: bool) -> dict:
        return {
            "event": event_type,
            "payload": {
                "payment": {
                    "entity": {
                        "id": payment_id,
                        "order_id": order.razorpay_order_id,
                        "amount": checkout.amount_paise,
                        "currency": checkout.currency,
                        "status": "captured" if captured else "failed",
                        "captured": captured,
                        "error_code": None if captured else "BAD_REQUEST_ERROR",
                        "error_description": None if captured else "Disclosed developer fixture",
                    }
                },
                "order": {"entity": order_entity},
            },
        }

    secret = settings.razorpay_webhook_secret.get_secret_value()

    def ingest(event_id: str, body: dict) -> bool:
        raw = canonical_json(body)
        signature = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
        event, created = persist_webhook(session, raw, signature, event_id, settings)
        if created:
            process_webhook(session, event)
        return created

    created_events = int(
        ingest(
            captured_id,
            fixture_payload("payment.captured", captured_payment_id, captured=True),
        )
    )
    failed_payload = fixture_payload("payment.failed", failed_payment_id, captured=False)
    created_events += int(ingest(failed_id, failed_payload))
    duplicate_created = ingest(failed_id, failed_payload)
    session.commit()
    return WebhookFixtureOut(
        checkout=checkout_out(checkout, order.razorpay_order_id),
        created_events=created_events,
        duplicate_deduplicated=not duplicate_created,
        disclosure="Signed developer fixture applied; this PAID state is not a provider payment.",
    )


@app.post("/v1/demo/login", response_model=LoginResponse)
def demo_login(payload: LoginRequest, session: Session = Depends(get_session)) -> LoginResponse:
    user = session.scalar(select(User).where(User.email == payload.email))
    if user is None or not verify_passcode(user.passcode_hash, payload.passcode):
        raise AuthorizationError("LOGIN_FAILED", "Email or demo passcode is incorrect", 401)
    if not settings.demo_auth_private_key_pem:
        raise TrustCartError("AUTH_NOT_CONFIGURED", "Demo auth signing key is missing", 503)
    token = issue_demo_token(
        user, load_private_key(settings.demo_auth_private_key_pem.get_secret_value())
    )
    return LoginResponse(access_token=token, user_id=user.id, display_name=user.display_name)


@app.post("/v1/agents", response_model=AgentOut)
def register_agent(
    payload: AgentCreate, _: User = Depends(merchant_user), session: Session = Depends(get_session)
) -> AgentOut:
    thumbprint = jwk_thumbprint(payload.public_jwk)
    existing = session.scalar(
        select(RegisteredAgent).where(
            RegisteredAgent.jwk_thumbprint == thumbprint,
            RegisteredAgent.key_version == payload.key_version,
        )
    )
    if existing is not None:
        return AgentOut.model_validate(existing)
    agent = RegisteredAgent(
        merchant_id=DEMO_MERCHANT_ID,
        name=payload.name,
        public_jwk=payload.public_jwk,
        jwk_thumbprint=thumbprint,
        key_version=payload.key_version,
    )
    session.add(agent)
    session.commit()
    return AgentOut.model_validate(agent)


@app.get("/v1/agents/current", response_model=AgentIdentityOut)
def current_agent(
    _: User = Depends(merchant_user), session: Session = Depends(get_session)
) -> AgentIdentityOut:
    agent = session.scalar(
        select(RegisteredAgent)
        .where(RegisteredAgent.status == "ACTIVE")
        .order_by(RegisteredAgent.created_at.desc())
        .limit(1)
    )
    if agent is None:
        raise TrustCartError("AGENT_NOT_REGISTERED", "No active agent is registered", 404)
    return AgentIdentityOut.model_validate(agent)


@app.post("/v1/grant-requests")
def create_grant_request(
    payload: GrantRequestCreate,
    agent_principal: RegisteredAgentPrincipal = Depends(require_registered_agent_proof),
    session: Session = Depends(get_session),
) -> dict:
    user = session.get(User, payload.user_id)
    if user is None:
        raise TrustCartError("USER_NOT_FOUND", "Requested user does not exist", 404)
    agent = session.get(RegisteredAgent, agent_principal.agent_id)
    if agent is None or agent.status != "ACTIVE":
        raise AuthorizationError("AGENT_NOT_ACTIVE", "Agent registration is not active", 403)
    if payload.cumulative_limit_paise < payload.per_order_limit_paise:
        raise TrustCartError("INVALID_CAPS", "Cumulative cap must cover the per-order cap", 422)
    if payload.expires_at <= datetime.now(UTC):
        raise TrustCartError("INVALID_EXPIRY", "Grant expiry must be in the future", 422)
    digest = sha256_hex(
        canonical_json(
            {
                **payload.model_dump(mode="json"),
                "merchant_id": str(agent.merchant_id),
                "agent_id": str(agent.id),
                "agent_thumbprint": agent.jwk_thumbprint,
            }
        )
    )
    request_row = GrantRequest(
        user_id=payload.user_id,
        merchant_id=agent.merchant_id,
        agent_id=agent.id,
        allowed_categories=payload.allowed_categories,
        per_order_limit_paise=payload.per_order_limit_paise,
        cumulative_limit_paise=payload.cumulative_limit_paise,
        valid_from=datetime.now(UTC),
        expires_at=payload.expires_at,
        auto_execute=payload.auto_execute,
        request_digest=digest,
    )
    session.add(request_row)
    session.commit()
    return {"id": str(request_row.id), "status": request_row.status, "request_digest": digest}


@app.post("/v1/grant-requests/{request_id}/approve", response_model=GrantOut)
def approve_grant(
    request_id: uuid.UUID,
    _: GrantApproval,
    user: User = Depends(merchant_user),
    session: Session = Depends(get_session),
) -> GrantOut:
    request_row = session.scalar(
        select(GrantRequest).where(GrantRequest.id == request_id).with_for_update()
    )
    if request_row is None or request_row.user_id != user.id:
        raise TrustCartError("GRANT_REQUEST_NOT_FOUND", "Grant request was not found", 404)
    existing = session.scalar(
        select(DelegationGrant).where(DelegationGrant.grant_request_id == request_row.id)
    )
    if existing:
        return GrantOut.model_validate(existing)
    agent = session.get(RegisteredAgent, request_row.agent_id)
    assert agent is not None
    immutable = {
        "user_id": str(user.id),
        "merchant_id": str(request_row.merchant_id),
        "agent_id": str(agent.id),
        "agent_key_thumbprint": agent.jwk_thumbprint,
        "allowed_categories": request_row.allowed_categories,
        "per_order_limit_paise": request_row.per_order_limit_paise,
        "cumulative_limit_paise": request_row.cumulative_limit_paise,
        "currency": request_row.currency,
        "valid_from": request_row.valid_from.isoformat(),
        "expires_at": request_row.expires_at.isoformat(),
        "auto_execute": request_row.auto_execute,
    }
    grant = DelegationGrant(
        grant_request_id=request_row.id,
        user_id=user.id,
        merchant_id=request_row.merchant_id,
        agent_id=agent.id,
        agent_key_thumbprint=agent.jwk_thumbprint,
        allowed_categories=request_row.allowed_categories,
        per_order_limit_paise=request_row.per_order_limit_paise,
        cumulative_limit_paise=request_row.cumulative_limit_paise,
        currency=request_row.currency,
        valid_from=request_row.valid_from,
        expires_at=request_row.expires_at,
        auto_execute=request_row.auto_execute,
        immutable_digest=sha256_hex(canonical_json(immutable)),
    )
    request_row.status = "APPROVED"
    session.add(grant)
    session.commit()
    return GrantOut.model_validate(grant)


@app.post("/v1/grants/{grant_id}/revoke", response_model=GrantOut)
def revoke_grant(
    grant_id: uuid.UUID,
    user: User = Depends(merchant_user),
    session: Session = Depends(get_session),
) -> GrantOut:
    grant = session.scalar(
        select(DelegationGrant).where(DelegationGrant.id == grant_id).with_for_update()
    )
    if grant is None or grant.user_id != user.id:
        raise TrustCartError("GRANT_NOT_FOUND", "Grant was not found", 404)
    grant.status, grant.revoked_at, grant.version = "REVOKED", datetime.now(UTC), grant.version + 1
    session.commit()
    return GrantOut.model_validate(grant)


@app.get("/v1/grants/{grant_id}", response_model=GrantOut)
def get_grant(
    grant_id: uuid.UUID,
    user: User = Depends(merchant_user),
    session: Session = Depends(get_session),
) -> GrantOut:
    grant = session.get(DelegationGrant, grant_id)
    if grant is None or grant.user_id != user.id:
        raise TrustCartError("GRANT_NOT_FOUND", "Grant was not found", 404)
    return GrantOut.model_validate(grant)


@app.get("/v1/grants", response_model=list[GrantOut])
def list_grants(
    user: User = Depends(merchant_user), session: Session = Depends(get_session)
) -> list[GrantOut]:
    rows = session.scalars(
        select(DelegationGrant)
        .where(DelegationGrant.user_id == user.id)
        .order_by(DelegationGrant.created_at.desc())
    ).all()
    return [GrantOut.model_validate(row) for row in rows]


@app.get("/v1/catalog/search", response_model=list[ProductOut])
def search_catalog(
    principal: AgentPrincipal = Depends(require_agent_proof),
    session: Session = Depends(get_session),
    query: str = "",
    category: str | None = None,
    max_unit_price_paise: int | None = None,
    limit: int = Query(default=10, ge=1, le=30),
) -> list[ProductOut]:
    statement = (
        select(Product, Inventory)
        .join(Inventory)
        .where(Product.merchant_id == principal.merchant_id, Product.active.is_(True))
    )
    if query:
        statement = statement.where(
            or_(Product.name.ilike(f"%{query}%"), Product.description.ilike(f"%{query}%"))
        )
    if category:
        if category not in principal.allowed_categories:
            raise AuthorizationError(
                "CATEGORY_NOT_ALLOWED", "Search category is outside the grant", 403
            )
        statement = statement.where(Product.category == category)
    if max_unit_price_paise is not None:
        statement = statement.where(Product.unit_price_paise <= max_unit_price_paise)
    return [
        ProductOut(
            id=p.id,
            sku=p.sku,
            name=p.name,
            description=p.description,
            category=p.category,
            unit_price_paise=p.unit_price_paise,
            tags=p.tags,
            available_quantity=i.on_hand_qty - i.reserved_qty,
        )
        for p, i in session.execute(statement.limit(limit))
    ]


@app.get("/v1/users/me/usual-basket")
def usual_basket(
    principal: AgentPrincipal = Depends(require_agent_proof),
    session: Session = Depends(get_session),
) -> dict:
    user = session.get(User, principal.user_id)
    basket = user.usual_basket if user else []
    quantities = {str(item["sku"]): int(item["quantity"]) for item in basket}
    if not quantities:
        return {"items": [], "estimated_subtotal_paise": 0}

    rows = session.execute(
        select(Product, Inventory)
        .join(Inventory, Inventory.product_id == Product.id)
        .where(
            Product.merchant_id == principal.merchant_id,
            Product.sku.in_(quantities),
        )
    ).all()
    by_sku = {product.sku: (product, inventory) for product, inventory in rows}
    items: list[dict] = []
    estimated_subtotal_paise = 0
    for basket_item in basket:
        sku = str(basket_item["sku"])
        quantity = int(basket_item["quantity"])
        row = by_sku.get(sku)
        if row is None:
            items.append(
                {
                    "sku": sku,
                    "quantity": quantity,
                    "catalog_status": "MISSING",
                }
            )
            continue
        product, inventory = row
        available_quantity = inventory.on_hand_qty - inventory.reserved_qty
        line_total_paise = product.unit_price_paise * quantity
        estimated_subtotal_paise += line_total_paise
        items.append(
            {
                "sku": sku,
                "quantity": quantity,
                "name": product.name,
                "category": product.category,
                "unit_price_paise": product.unit_price_paise,
                "line_total_paise": line_total_paise,
                "available_quantity": available_quantity,
                "within_grant_scope": product.category in principal.allowed_categories,
                "catalog_status": "AVAILABLE" if available_quantity >= quantity else "LOW_STOCK",
            }
        )
    return {
        "items": items,
        "estimated_subtotal_paise": estimated_subtotal_paise,
        "estimate_excludes_delivery": True,
        "default_delivery_postcode": user.default_postcode if user else None,
    }


@app.post("/v1/delivery-options", response_model=list[DeliveryOptionOut])
def delivery_options(
    payload: DeliveryOptionRequest, _: AgentPrincipal = Depends(require_agent_proof)
) -> list[DeliveryOptionOut]:
    now = datetime.now(UTC)
    return [
        DeliveryOptionOut(
            id=key,
            label=value["label"],
            window=value["window"],
            fee_paise=value["fee_paise"],
            cutoff_at=now.replace(hour=17, minute=0, second=0, microsecond=0),
        )
        for key, value in DELIVERY_OPTIONS.items()
    ]


@app.post("/v1/quotes", response_model=QuoteOut)
def quote(
    payload: QuoteCreate,
    principal: AgentPrincipal = Depends(require_agent_proof),
    session: Session = Depends(get_session),
) -> QuoteOut:
    result = create_quote(session, principal, payload)
    session.commit()
    return result


@app.get("/v1/quotes/{quote_id}", response_model=QuoteOut)
def get_quote(
    quote_id: uuid.UUID,
    principal: AgentPrincipal = Depends(require_agent_proof),
    session: Session = Depends(get_session),
) -> QuoteOut:
    row = session.get(Quote, quote_id)
    if row is None or (row.user_id, row.agent_id, row.grant_id) != (
        principal.user_id,
        principal.agent_id,
        principal.grant_id,
    ):
        raise TrustCartError("QUOTE_NOT_FOUND", "Quote was not found", 404)
    grant = session.get(DelegationGrant, principal.grant_id)
    assert grant is not None
    remaining = grant.cumulative_limit_paise - grant.spent_paise - grant.held_paise
    return quote_out(row, remaining)


@app.post("/v1/checkouts", response_model=CheckoutOut)
def checkout(
    payload: CheckoutCreate,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=16, max_length=128)],
    principal: AgentPrincipal = Depends(require_agent_proof),
    session: Session = Depends(get_session),
) -> CheckoutOut:
    row = create_checkout(session, principal, payload, idempotency_key)
    session.commit()
    return checkout_out(row)


@app.get("/v1/checkouts/{checkout_id}", response_model=CheckoutOut)
def get_checkout(
    checkout_id: uuid.UUID,
    principal: AgentPrincipal = Depends(require_agent_proof),
    session: Session = Depends(get_session),
) -> CheckoutOut:
    row = session.get(Checkout, checkout_id)
    if row is None or (row.agent_id, row.grant_id) != (principal.agent_id, principal.grant_id):
        raise TrustCartError("CHECKOUT_NOT_FOUND", "Checkout was not found", 404)
    order = session.scalar(select(RazorpayOrder).where(RazorpayOrder.checkout_id == row.id))
    return checkout_out(row, order.razorpay_order_id if order else None)


@app.post("/v1/checkouts/{checkout_id}/cancel", response_model=CheckoutOut)
def cancel_checkout(
    checkout_id: uuid.UUID,
    principal: AgentPrincipal = Depends(require_agent_proof),
    session: Session = Depends(get_session),
) -> CheckoutOut:
    row = session.scalar(select(Checkout).where(Checkout.id == checkout_id).with_for_update())
    if row is None or (row.agent_id, row.grant_id) != (principal.agent_id, principal.grant_id):
        raise TrustCartError("CHECKOUT_NOT_FOUND", "Checkout was not found", 404)
    if row.status == CheckoutStatus.CANCEL_WINDOW:
        release_reservations(session, row, CheckoutStatus.CANCELLED, "USER_CANCELLED_DURING_WINDOW")
    elif row.status == CheckoutStatus.PAYMENT_PENDING:
        row.status, row.version = CheckoutStatus.EXPIRING, row.version + 1
    else:
        raise TrustCartError("CHECKOUT_NOT_CANCELLABLE", "Checkout is no longer cancellable", 409)
    session.commit()
    return checkout_out(row)


@app.get("/v1/audit-events", response_model=list[AuditEventOut])
def audit_events(
    checkout_id: uuid.UUID,
    user: User = Depends(merchant_user),
    session: Session = Depends(get_session),
) -> list[AuditEventOut]:
    checkout = session.get(Checkout, checkout_id)
    if checkout is None or checkout.user_id != user.id:
        raise TrustCartError("CHECKOUT_NOT_FOUND", "Checkout was not found", 404)
    return [
        AuditEventOut.model_validate(item)
        for item in session.scalars(
            select(AuditEvent)
            .where(AuditEvent.checkout_id == checkout_id)
            .order_by(AuditEvent.sequence)
        )
    ]


@app.post("/v1/webhooks/razorpay")
async def razorpay_webhook(
    request: Request,
    x_razorpay_signature: Annotated[str, Header(alias="X-Razorpay-Signature")],
    x_razorpay_event_id: Annotated[str, Header(alias="X-Razorpay-Event-Id")],
    session: Session = Depends(get_session),
) -> dict:
    # Deliberately no session-token or agent-PoP dependency on this route.
    raw = await request.body()
    event, created = persist_webhook(
        session, raw, x_razorpay_signature, x_razorpay_event_id, settings
    )
    if created:
        process_webhook(session, event)
    session.commit()
    return {"accepted": True, "duplicate": not created}
