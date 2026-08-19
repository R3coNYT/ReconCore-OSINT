"""Symmetric encryption of plugin secrets (session cookies, API keys).

Secrets never travel to the API in clear text and never reach the logs. The key
comes from `SECRETS_ENCRYPTION_KEY`; without it, writing a secret is refused
explicitly rather than silently degraded.
"""
from __future__ import annotations

from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings


class SecretsUnavailable(RuntimeError):
    """SECRETS_ENCRYPTION_KEY is missing or invalid."""


@lru_cache
def _fernet() -> Fernet:
    key = settings.secrets_encryption_key.strip()
    if not key:
        raise SecretsUnavailable(
            "SECRETS_ENCRYPTION_KEY is not set: cannot store a secret. "
            'Generate a key: python -c "from cryptography.fernet import Fernet; '
            'print(Fernet.generate_key().decode())"'
        )
    try:
        return Fernet(key.encode("utf-8"))
    except (ValueError, TypeError) as exc:
        raise SecretsUnavailable("SECRETS_ENCRYPTION_KEY is not a valid Fernet key") from exc


def secrets_available() -> bool:
    try:
        _fernet()
    except SecretsUnavailable:
        return False
    return True


def encrypt(value: str) -> str:
    return _fernet().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt(ciphertext: str) -> str:
    try:
        return _fernet().decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise SecretsUnavailable(
            "Cannot decrypt: the key changed or the stored value is corrupted"
        ) from exc


def mask(value: str, keep: int = 4) -> str:
    """Represent a secret without revealing it (UI and CLI display)."""
    if not value:
        return ""
    if len(value) <= keep:
        return "*" * len(value)
    return f"{'*' * (len(value) - keep)}{value[-keep:]}"
