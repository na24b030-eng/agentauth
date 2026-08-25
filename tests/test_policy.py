import uuid

from trustcart.commerce import semantic_checkout_hash
from trustcart.enums import PaymentMode
from trustcart.pop import AgentPrincipal
from trustcart.schemas import CheckoutCreate


def principal() -> AgentPrincipal:
    return AgentPrincipal(
        agent_id=uuid.uuid4(),
        grant_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        merchant_id=uuid.uuid4(),
        grant_digest="d" * 64,
        allowed_categories=("dairy",),
        auto_execute=True,
    )


def test_semantic_hash_uses_only_purchase_identity() -> None:
    authority = principal()
    quote_id = uuid.uuid4()
    payload = CheckoutCreate(
        quote_id=quote_id,
        payment_mode=PaymentMode.DELEGATED_DEBIT_SIMULATOR,
    )
    assert semantic_checkout_hash(authority, payload) == semantic_checkout_hash(authority, payload)

    changed = CheckoutCreate(
        quote_id=uuid.uuid4(),
        payment_mode=PaymentMode.DELEGATED_DEBIT_SIMULATOR,
    )
    assert semantic_checkout_hash(authority, payload) != semantic_checkout_hash(authority, changed)


def test_checkout_payload_rejects_model_controlled_fields() -> None:
    payload = {
        "quote_id": str(uuid.uuid4()),
        "payment_mode": "DELEGATED_DEBIT_SIMULATOR",
        "price_paise": 1,
    }
    try:
        CheckoutCreate.model_validate(payload)
    except ValueError as exc:
        assert "price_paise" in str(exc)
    else:
        raise AssertionError("unknown money fields must be rejected")
