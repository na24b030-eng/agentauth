from __future__ import annotations

import base64
import hashlib
import json
import secrets
import urllib.parse
from dataclasses import dataclass

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import (
    decode_dss_signature,
    encode_dss_signature,
)


def b64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def b64url_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def canonical_json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def sha256_hex(value: bytes | str) -> str:
    raw = value.encode() if isinstance(value, str) else value
    return hashlib.sha256(raw).hexdigest()


def generate_p256_private_key() -> ec.EllipticCurvePrivateKey:
    return ec.generate_private_key(ec.SECP256R1())


def private_key_to_pem(key: ec.EllipticCurvePrivateKey) -> str:
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()


def public_key_to_pem(key: ec.EllipticCurvePublicKey) -> str:
    return key.public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()


def public_key_to_jwk(key: ec.EllipticCurvePublicKey) -> dict[str, str]:
    numbers = key.public_numbers()
    return {
        "kty": "EC",
        "crv": "P-256",
        "x": b64url_encode(numbers.x.to_bytes(32, "big")),
        "y": b64url_encode(numbers.y.to_bytes(32, "big")),
    }


def public_key_from_jwk(jwk: dict[str, str]) -> ec.EllipticCurvePublicKey:
    if jwk.get("kty") != "EC" or jwk.get("crv") != "P-256":
        raise ValueError("Only P-256 EC keys are supported")
    numbers = ec.EllipticCurvePublicNumbers(
        int.from_bytes(b64url_decode(jwk["x"]), "big"),
        int.from_bytes(b64url_decode(jwk["y"]), "big"),
        ec.SECP256R1(),
    )
    return numbers.public_key()


def jwk_thumbprint(jwk: dict[str, str]) -> str:
    required = {key: jwk[key] for key in ("crv", "kty", "x", "y")}
    return b64url_encode(hashlib.sha256(canonical_json(required)).digest())


def load_private_key(pem: str) -> ec.EllipticCurvePrivateKey:
    key = serialization.load_pem_private_key(pem.encode(), password=None)
    if not isinstance(key, ec.EllipticCurvePrivateKey) or not isinstance(key.curve, ec.SECP256R1):
        raise ValueError("Expected a P-256 private key")
    return key


def sign_es256(key: ec.EllipticCurvePrivateKey, content: bytes) -> str:
    der = key.sign(content, ec.ECDSA(hashes.SHA256()))
    r, s = decode_dss_signature(der)
    return b64url_encode(r.to_bytes(32, "big") + s.to_bytes(32, "big"))


def verify_es256(key: ec.EllipticCurvePublicKey, content: bytes, signature: str) -> None:
    raw = b64url_decode(signature)
    if len(raw) != 64:
        raise ValueError("Invalid ES256 signature length")
    der = encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big"))
    key.verify(der, content, ec.ECDSA(hashes.SHA256()))


def canonical_query(query_items: list[tuple[str, str]]) -> str:
    encoded = [
        (urllib.parse.quote(str(key), safe="~"), urllib.parse.quote(str(value), safe="~"))
        for key, value in query_items
    ]
    return "&".join(f"{key}={value}" for key, value in sorted(encoded))


def build_proof_content(
    *,
    method: str,
    path: str,
    query_items: list[tuple[str, str]],
    body_sha256: str,
    timestamp: int,
    nonce: str,
    grant_id: str | None = None,
    grant_digest: str | None = None,
) -> bytes:
    if grant_id and grant_digest:
        fields = [
            "TC-POP-V1",
            method.upper(),
            path,
            canonical_query(query_items),
            body_sha256,
            str(timestamp),
            nonce,
            grant_id,
            grant_digest,
        ]
    else:
        fields = [
            "TC-AGENT-V1",
            method.upper(),
            path,
            canonical_query(query_items),
            body_sha256,
            str(timestamp),
            nonce,
        ]
    return "\n".join(fields).encode()


def new_nonce() -> str:
    return b64url_encode(secrets.token_bytes(24))


@dataclass(frozen=True, slots=True)
class SignedProof:
    timestamp: int
    nonce: str
    body_sha256: str
    signature: str


def create_proof(
    key: ec.EllipticCurvePrivateKey,
    *,
    method: str,
    path: str,
    query_items: list[tuple[str, str]],
    body: bytes,
    timestamp: int,
    nonce: str | None = None,
    grant_id: str | None = None,
    grant_digest: str | None = None,
) -> SignedProof:
    nonce = nonce or new_nonce()
    body_digest = sha256_hex(body)
    content = build_proof_content(
        method=method,
        path=path,
        query_items=query_items,
        body_sha256=body_digest,
        timestamp=timestamp,
        nonce=nonce,
        grant_id=grant_id,
        grant_digest=grant_digest,
    )
    return SignedProof(timestamp, nonce, body_digest, sign_es256(key, content))
