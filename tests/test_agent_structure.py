import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import trustcart.agent_runtime as agent_runtime
from google.genai import errors as genai_errors
from google.genai import types
from trustcart.agent_runtime import (
    PLACE_ORDER_TOOL,
    CommerceRunContext,
    GeminiCommerceAgent,
    build_agent,
    execute_tool,
    replay_nonce_fixture,
)
from trustcart.crypto import generate_p256_private_key, private_key_to_pem
from trustcart.errors import TrustCartError


def context(*, quoted: bool, auto_execute: bool) -> CommerceRunContext:
    return CommerceRunContext(
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


def test_place_order_has_no_model_controlled_arguments() -> None:
    assert PLACE_ORDER_TOOL.params_json_schema["properties"] == {}
    assert PLACE_ORDER_TOOL.params_json_schema["additionalProperties"] is False
    declaration = PLACE_ORDER_TOOL.declaration()
    assert declaration.parameters is not None
    assert declaration.parameters_json_schema is None


def test_place_order_visibility_is_structural() -> None:
    agent = build_agent()
    before_quote = {tool.name for tool in agent.visible_tools(context(quoted=False, auto_execute=True))}
    approval = {tool.name for tool in agent.visible_tools(context(quoted=True, auto_execute=False))}
    execution = {tool.name for tool in agent.visible_tools(context(quoted=True, auto_execute=True))}
    assert "place_order" not in before_quote
    assert "place_order" not in approval
    assert "request_purchase_approval" in approval
    assert "place_order" in execution


def test_expired_quote_disables_place_order() -> None:
    ctx = context(quoted=True, auto_execute=True)
    assert ctx.active_quote is not None
    ctx.active_quote["expires_at"] = (
        datetime.now(UTC) - timedelta(seconds=1)
    ).isoformat()
    assert "place_order" not in {tool.name for tool in build_agent().visible_tools(ctx)}


def test_completed_money_action_disables_every_model_tool() -> None:
    checkout = context(quoted=True, auto_execute=True)
    checkout.checkout_created = True
    approval = context(quoted=True, auto_execute=False)
    approval.approval_requested = True

    assert build_agent().visible_tools(checkout) == ()
    assert build_agent().visible_tools(approval) == ()


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
async def test_unavailable_action_tool_is_rejected_without_execution() -> None:
    ctx = context(quoted=False, auto_execute=True)
    ctx.persist_events = False
    result = await execute_tool(ctx, "place_order", {})
    assert result["ok"] is False
    assert result["error"]["code"] == "TOOL_NOT_AVAILABLE"
    assert ctx.checkout_created is False


@pytest.mark.asyncio
async def test_gemini_loop_executes_one_declared_tool_then_returns_text() -> None:
    responses = [
        types.GenerateContentResponse(
            candidates=[
                types.Candidate(
                    content=types.Content(
                        role="model",
                        parts=[
                            types.Part(
                                function_call=types.FunctionCall(
                                    id="call-1", name="get_usual_basket", args={}
                                )
                            )
                        ],
                    )
                )
            ],
            usage_metadata=types.GenerateContentResponseUsageMetadata(
                prompt_token_count=10, total_token_count=15
            ),
        ),
        types.GenerateContentResponse(
            candidates=[
                types.Candidate(
                    content=types.Content(
                        role="model",
                        parts=[types.Part.from_text(text="I found the usual basket.")],
                    )
                )
            ],
            usage_metadata=types.GenerateContentResponseUsageMetadata(
                prompt_token_count=20, total_token_count=27
            ),
        ),
    ]
    seen_configs: list[types.GenerateContentConfig] = []

    class FakeModels:
        async def generate_content(self, *, config, **_):
            seen_configs.append(config)
            return responses.pop(0)

    class FakeAio:
        models = FakeModels()

        async def aclose(self):
            return None

    class FakeClient:
        aio = FakeAio()

    async def merchant(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/usual-basket")
        return httpx.Response(200, json={"items": [{"sku": "MILK-1L", "quantity": 2}]})

    ctx = context(quoted=False, auto_execute=True)
    ctx.transport = httpx.MockTransport(merchant)
    ctx.signing_key_pem = private_key_to_pem(generate_p256_private_key())
    ctx.persist_events = False
    agent = GeminiCommerceAgent(client_factory=lambda **_: FakeClient())

    result = await agent.run("What do I usually buy?", ctx, api_key="test-key")

    assert result.final_output == "I found the usual basket."
    assert result.turns == 2
    assert result.input_tokens == 30
    assert result.output_tokens == 12
    assert ctx.tool_calls == 1
    assert seen_configs[0].automatic_function_calling.disable is True


@pytest.mark.asyncio
async def test_successful_checkout_returns_terminal_result_without_an_extra_model_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = types.GenerateContentResponse(
        candidates=[
            types.Candidate(
                content=types.Content(
                    role="model",
                    parts=[
                        types.Part(
                            function_call=types.FunctionCall(
                                id="call-1", name="place_order", args={}
                            )
                        )
                    ],
                )
            )
        ]
    )
    model_calls = 0

    class FakeModels:
        async def generate_content(self, **_):
            nonlocal model_calls
            model_calls += 1
            return response

    class FakeAio:
        models = FakeModels()

        async def aclose(self):
            return None

    class FakeClient:
        aio = FakeAio()

    async def successful_checkout(
        ctx: CommerceRunContext, name: str, raw_arguments: dict
    ) -> dict:
        assert name == "place_order"
        assert raw_arguments == {}
        ctx.tool_calls += 1
        ctx.checkout_created = True
        return {"ok": True, "result": {"status": "CANCEL_WINDOW"}}

    monkeypatch.setattr(agent_runtime, "execute_tool", successful_checkout)
    ctx = context(quoted=True, auto_execute=True)
    ctx.persist_events = False
    agent = GeminiCommerceAgent(client_factory=lambda **_: FakeClient())

    result = await agent.run("Place the quoted order", ctx, api_key="test-key")

    assert result.turns == 1
    assert model_calls == 1
    assert ctx.checkout_created is True
    assert result.final_output.startswith("Checkout scheduled")


@pytest.mark.asyncio
async def test_parallel_function_calls_are_rejected_without_running_tools() -> None:
    responses = [
        types.GenerateContentResponse(
            candidates=[
                types.Candidate(
                    content=types.Content(
                        role="model",
                        parts=[
                            types.Part(
                                function_call=types.FunctionCall(
                                    id="call-1", name="get_usual_basket", args={}
                                )
                            ),
                            types.Part(
                                function_call=types.FunctionCall(
                                    id="call-2",
                                    name="get_delivery_options",
                                    args={"postcode": "400001"},
                                )
                            ),
                        ],
                    )
                )
            ]
        ),
        types.GenerateContentResponse(
            candidates=[
                types.Candidate(
                    content=types.Content(
                        role="model",
                        parts=[types.Part.from_text(text="I need to retry sequentially.")],
                    )
                )
            ]
        ),
    ]

    class FakeModels:
        async def generate_content(self, **_):
            return responses.pop(0)

    class FakeAio:
        models = FakeModels()

        async def aclose(self):
            return None

    class FakeClient:
        aio = FakeAio()

    ctx = context(quoted=False, auto_execute=True)
    ctx.persist_events = False
    agent = GeminiCommerceAgent(client_factory=lambda **_: FakeClient())

    result = await agent.run("Order my usual groceries", ctx, api_key="test-key")

    assert result.final_output == "I need to retry sequentially."
    assert result.turns == 2
    assert ctx.tool_calls == 0


@pytest.mark.asyncio
async def test_gemini_free_tier_quota_failure_is_typed() -> None:
    class RateLimitedModels:
        async def generate_content(self, **_):
            raise genai_errors.ClientError(429, {"error": {"message": "quota"}})

    class FakeAio:
        models = RateLimitedModels()

        async def aclose(self):
            return None

    class FakeClient:
        aio = FakeAio()

    ctx = context(quoted=False, auto_execute=True)
    ctx.persist_events = False
    agent = GeminiCommerceAgent(client_factory=lambda **_: FakeClient())

    with pytest.raises(TrustCartError) as raised:
        await agent.run("Order milk", ctx, api_key="test-key")

    assert raised.value.code == "MODEL_RATE_LIMITED"


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
