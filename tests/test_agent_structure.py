import uuid
from datetime import UTC, datetime, timedelta

from agents import RunContextWrapper
from trustcart.agent_runtime import CommerceRunContext, build_agent, place_order


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
