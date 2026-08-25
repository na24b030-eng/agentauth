import time

import pytest
from cryptography.exceptions import InvalidSignature
from trustcart.crypto import (
    build_proof_content,
    create_proof,
    generate_p256_private_key,
    jwk_thumbprint,
    public_key_from_jwk,
    public_key_to_jwk,
    verify_es256,
)


def test_es256_proof_binds_every_request_component() -> None:
    key = generate_p256_private_key()
    timestamp = int(time.time())
    proof = create_proof(
        key,
        method="POST",
        path="/v1/checkouts",
        query_items=[("b", "2"), ("a", "1")],
        body=b'{"quote_id":"q"}',
        timestamp=timestamp,
        nonce="nonce-which-is-long-enough",
        grant_id="grant-1",
        grant_digest="digest-1",
    )
    content = build_proof_content(
        method="POST",
        path="/v1/checkouts",
        query_items=[("a", "1"), ("b", "2")],
        body_sha256=proof.body_sha256,
        timestamp=timestamp,
        nonce=proof.nonce,
        grant_id="grant-1",
        grant_digest="digest-1",
    )
    verify_es256(key.public_key(), content, proof.signature)

    tampered = content.replace(b"/v1/checkouts", b"/v1/quotes")
    with pytest.raises(InvalidSignature):
        verify_es256(key.public_key(), tampered, proof.signature)


def test_jwk_thumbprint_is_stable_and_round_trips() -> None:
    key = generate_p256_private_key()
    jwk = public_key_to_jwk(key.public_key())
    assert jwk_thumbprint(jwk) == jwk_thumbprint(dict(reversed(list(jwk.items()))))
    proof = create_proof(
        key,
        method="GET",
        path="/health",
        query_items=[],
        body=b"",
        timestamp=1,
        nonce="nonce-which-is-long-enough",
    )
    content = build_proof_content(
        method="GET",
        path="/health",
        query_items=[],
        body_sha256=proof.body_sha256,
        timestamp=1,
        nonce=proof.nonce,
    )
    verify_es256(public_key_from_jwk(jwk), content, proof.signature)
