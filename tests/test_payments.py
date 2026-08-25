import hashlib
import hmac

from trustcart.payments import verify_razorpay_webhook


def test_webhook_hmac_accepts_current_or_previous_secret() -> None:
    body = b'{"event":"payment.captured"}'
    previous = "previous-secret"
    signature = hmac.new(previous.encode(), body, hashlib.sha256).hexdigest()
    assert verify_razorpay_webhook(body, signature, ["current-secret", previous])
    assert not verify_razorpay_webhook(body + b" ", signature, ["current-secret", previous])
