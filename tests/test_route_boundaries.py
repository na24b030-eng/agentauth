import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from trustcart.agent_api import app as agent_app
from trustcart.agent_api import start_run
from trustcart.auth import agent_user, merchant_user
from trustcart.enums import PaymentMode, RunStatus
from trustcart.merchant_api import app
from trustcart.pop import require_agent_proof
from trustcart.schemas import AgentRunCreate


def test_razorpay_webhook_is_outside_agent_pop_dependency() -> None:
    route = next(route for route in app.routes if route.path == "/v1/webhooks/razorpay")
    dependencies = {dependency.call for dependency in route.dependant.dependencies}
    assert require_agent_proof not in dependencies


def test_quote_readback_stays_behind_agent_pop() -> None:
    route = next(route for route in app.routes if route.path == "/v1/quotes/{quote_id}")
    dependencies = {dependency.call for dependency in route.dependant.dependencies}
    assert require_agent_proof in dependencies


def test_publishable_payment_config_is_not_a_pop_route() -> None:
    route = next(route for route in app.routes if route.path == "/v1/payment-config")
    dependencies = {dependency.call for dependency in route.dependant.dependencies}
    assert require_agent_proof not in dependencies


def test_failure_fixture_requires_user_session_and_not_agent_pop() -> None:
    route = next(
        route for route in app.routes if route.path == "/v1/developer/faults/{fault_key}"
    )
    dependencies = {dependency.call for dependency in route.dependant.dependencies}
    assert merchant_user in dependencies
    assert require_agent_proof not in dependencies


def test_demo_reset_requires_user_session_and_not_agent_pop() -> None:
    route = next(
        route for route in app.routes if route.path == "/v1/developer/reset-demo"
    )
    dependencies = {dependency.call for dependency in route.dependant.dependencies}
    assert merchant_user in dependencies
    assert require_agent_proof not in dependencies


def test_nonce_replay_fixture_requires_agent_user_session() -> None:
    route = next(
        route for route in agent_app.routes if route.path == "/v1/developer/replay-nonce"
    )
    dependencies = {dependency.call for dependency in route.dependant.dependencies}
    assert agent_user in dependencies
    assert require_agent_proof not in dependencies


def test_webhook_fixture_requires_merchant_user_session() -> None:
    route = next(
        route for route in app.routes if route.path == "/v1/developer/webhook-fixture"
    )
    dependencies = {dependency.call for dependency in route.dependant.dependencies}
    assert merchant_user in dependencies
    assert require_agent_proof not in dependencies


@pytest.mark.asyncio
async def test_start_run_returns_durable_queue_state_without_running_model_inline() -> None:
    user_id, grant_id, agent_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    now = datetime.now(UTC)
    grant = SimpleNamespace(
        id=grant_id,
        user_id=user_id,
        agent_id=agent_id,
        status="ACTIVE",
        valid_from=now - timedelta(minutes=1),
        expires_at=now + timedelta(minutes=5),
    )

    class FakeSession:
        def get(self, _model, key):
            return grant if key == grant_id else None

        def add(self, row):
            row.id = uuid.uuid4()
            row.status = RunStatus.QUEUED
            row.tool_call_count = row.turn_count = 0
            row.final_response = row.active_quote_id = row.checkout_id = row.error_code = None

        def commit(self):
            return None

        def refresh(self, _row):
            return None

    result = await start_run(
        AgentRunCreate(
            message="Order milk",
            payment_mode=PaymentMode.DELEGATED_DEBIT_SIMULATOR,
            grant_id=grant_id,
        ),
        SimpleNamespace(id=user_id),
        FakeSession(),
    )
    assert result.status == RunStatus.QUEUED
