import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from agents import RunContextWrapper
from trustcart.agent_runtime import (
    CommerceRunContext,
    build_agent,
    place_order,
    replay_nonce_fixture,
)
from trustcart.crypto import generate_p256_private_key, private_key_to_pem


def context(*, quoted: bool, auto_execute: bool) -> RunContextWrapper[CommerceRunContext]:
    value = CommerceRunContext(
        run_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        agent_id=uuid.uuid4(),
        grant_id=uuid.uuid4(),
        grant_digest="d" * 64,
        auto_execute=auto_execute,
        payment_mode="DELEGATED_DEBIT_SIMULATOR",
        active_quote={
            "id": str(uuid.uuid4()),
            "expires_at": (datetime.now(UTC) + timedelta(minutes=1)).isoformat(),
        }
        if quoted
        else None,
    )
    return RunContextWrapper(value)


def test_place_order_has_no_model_controlled_arguments() -> None:
    assert place_order.params_json_schema["properties"] == {}
    assert place_order.params_json_schema["additionalProperties"] is False


def test_place_order_visibility_is_structural() -> None:
    agent = build_agent()
    assert place_order.is_enabled(context(quoted=False, auto_execute=True), agent) is False
    assert place_order.is_enabled(context(quoted=True, auto_execute=False), agent) is False
    assert place_order.is_enabled(context(quoted=True, auto_execute=True), agent) is True


def test_expired_quote_disables_place_order() -> None:
    wrapper = context(quoted=True, auto_execute=True)
    wrapper.context.active_quote["expires_at"] = (
        datetime.now(UTC) - timedelta(seconds=1)
    ).isoformat()
    assert place_order.is_enabled(wrapper, build_agent()) is False


def test_single_agent_contains_only_bounded_commerce_tools() -> None:
    names = {tool.name for tool in build_agent().tools}
    assert names == {
        "search_catalog",
        "get_usual_basket",
        "get_delivery_options",
        "quote_cart",
        "place_order",
        "request_purchase_approval",
    }


@pytest.mark.asyncio
async def test_nonce_replay_fixture_reuses_exact_proof_and_reports_rejection() -> None:
    seen_nonces: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen_nonces.append(request.headers["X-Agent-Nonce"])
        if len(seen_nonces) == 1:
            return httpx.Response(200, json=[])
        return httpx.Response(
            409,
            json={"code": "PROOF_REPLAYED", "message": "Proof nonce was already used"},
        )

    ctx = CommerceRunContext(
        run_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        agent_id=uuid.uuid4(),
        grant_id=uuid.uuid4(),
        grant_digest="d" * 64,
        auto_execute=True,
        payment_mode="DELEGATED_DEBIT_SIMULATOR",
        transport=httpx.MockTransport(handler),
        signing_key_pem=private_key_to_pem(generate_p256_private_key()),
        persist_events=False,
    )

    result = await replay_nonce_fixture(ctx)

    assert seen_nonces[0] == seen_nonces[1]
    assert result == {
        "first_status": 200,
        "second_status": 409,
        "second_code": "PROOF_REPLAYED",
        "proof_replayed": True,
    }
