import hashlib
import hmac

from trustcart.payments import provider_facts_match, verify_razorpay_webhook


def test_webhook_hmac_accepts_current_or_previous_secret() -> None:
    body = b'{"event":"payment.captured"}'
    previous = "previous-secret"
    signature = hmac.new(previous.encode(), body, hashlib.sha256).hexdigest()
    assert verify_razorpay_webhook(body, signature, ["current-secret", previous])
    assert not verify_razorpay_webhook(body + b" ", signature, ["current-secret", previous])


def test_provider_facts_must_exactly_match_canonical_checkout() -> None:
    assert provider_facts_match(84_200, "INR", 84_200, "INR")
    assert not provider_facts_match(84_200, "INR", 8_420, "INR")
    assert not provider_facts_match(84_200, "INR", 84_200, "USD")
