from trustcart.merchant_api import app
from trustcart.pop import require_agent_proof


def test_razorpay_webhook_is_outside_agent_pop_dependency() -> None:
    route = next(route for route in app.routes if route.path == "/v1/webhooks/razorpay")
    dependencies = {dependency.call for dependency in route.dependant.dependencies}
    assert require_agent_proof not in dependencies
