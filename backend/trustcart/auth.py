from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import jwt
from argon2 import PasswordHasher
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import Depends, Header
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import Settings, get_settings
from .crypto import public_key_to_pem
from .db import get_session
from .errors import AuthorizationError
from .models import User

password_hasher = PasswordHasher()


def hash_passcode(passcode: str) -> str:
    return password_hasher.hash(passcode)


def verify_passcode(passcode_hash: str, passcode: str) -> bool:
    try:
        return password_hasher.verify(passcode_hash, passcode)
    except Exception:
        return False


def issue_demo_token(
    user: User, private_key: ec.EllipticCurvePrivateKey, *, now: datetime | None = None
) -> str:
    now = now or datetime.now(UTC)
    payload = {
        "iss": "trustcart-demo-auth",
        "sub": str(user.id),
        "aud": ["merchant-api", "agent-api"],
        "iat": now,
        "exp": now + timedelta(minutes=30),
        "jti": str(uuid.uuid4()),
        "demo_identity": True,
    }
    return jwt.encode(payload, private_key, algorithm="ES256")


def decode_demo_token(token: str, public_key_pem: str, audience: str) -> dict:
    try:
        return jwt.decode(
            token,
            public_key_pem,
            algorithms=["ES256"],
            audience=audience,
            issuer="trustcart-demo-auth",
        )
    except jwt.PyJWTError as exc:
        raise AuthorizationError(
            "INVALID_SESSION", "The demo session is invalid or expired", 401
        ) from exc


def demo_public_key_pem(settings: Settings) -> str:
    if settings.demo_auth_public_key_pem:
        return settings.demo_auth_public_key_pem
    if settings.demo_auth_private_key_pem:
        from .crypto import load_private_key

        return public_key_to_pem(
            load_private_key(settings.demo_auth_private_key_pem.get_secret_value()).public_key()
        )
    raise RuntimeError("Demo auth public key is not configured")


def current_user_dependency(audience: str):
    def dependency(
        authorization: str | None = Header(default=None),
        session: Session = Depends(get_session),
        settings: Settings = Depends(get_settings),
    ) -> User:
        if not authorization or not authorization.startswith("Bearer "):
            raise AuthorizationError("SESSION_REQUIRED", "A demo session is required", 401)
        claims = decode_demo_token(
            authorization.removeprefix("Bearer "), demo_public_key_pem(settings), audience
        )
        user = session.scalar(select(User).where(User.id == uuid.UUID(claims["sub"])))
        if user is None:
            raise AuthorizationError("USER_NOT_FOUND", "The demo user no longer exists", 401)
        return user

    return dependency


merchant_user = current_user_dependency("merchant-api")
agent_user = current_user_dependency("agent-api")
