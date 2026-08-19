"""Issuing and verifying access JWTs and (opaque) refresh tokens."""
from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta

import jwt

from app.core.config import settings

ALGORITHM = "HS256"


class TokenError(Exception):
    """Token is missing, expired, malformed or revoked."""


def _now() -> datetime:
    return datetime.now(UTC)


def create_access_token(
    user_id: uuid.UUID | str, role: str, expires_minutes: int | None = None
) -> tuple[str, datetime]:
    expire = _now() + timedelta(
        minutes=expires_minutes or settings.access_token_expire_minutes
    )
    payload = {
        "sub": str(user_id),
        "role": role,
        "type": "access",
        "iat": int(_now().timestamp()),
        "exp": int(expire.timestamp()),
        "jti": secrets.token_hex(8),
    }
    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM), expire


def decode_access_token(token: str) -> dict:
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError as exc:
        raise TokenError("expired token") from exc
    except jwt.InvalidTokenError as exc:
        raise TokenError("invalid token") from exc
    if payload.get("type") != "access":
        raise TokenError("unexpected token type")
    return payload


def create_refresh_token() -> tuple[str, str, datetime]:
    """Return (plaintext token, stored hash, expiry).

    The plaintext is never persisted: only its SHA-256 digest reaches the
    database.
    """
    raw = secrets.token_urlsafe(48)
    expire = _now() + timedelta(days=settings.refresh_token_expire_days)
    return raw, hash_refresh_token(raw), expire


def hash_refresh_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
