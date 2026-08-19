"""Password hashing with Argon2id (OWASP 2024 parameters)."""
from __future__ import annotations

import secrets
import string

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from argon2.low_level import Type

#: memory_cost 64 MiB, time_cost 3, parallelism 4: the OWASP-recommended balance.
_hasher = PasswordHasher(
    time_cost=3, memory_cost=65536, parallelism=4, hash_len=32, salt_len=16, type=Type.ID
)

MIN_PASSWORD_LENGTH = 12


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    try:
        return _hasher.verify(hashed, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(hashed: str) -> bool:
    """True when the hash uses outdated parameters (re-hash it on next login)."""
    try:
        return _hasher.check_needs_rehash(hashed)
    except InvalidHashError:
        return True


def validate_password_strength(password: str) -> list[str]:
    """Return the rules the password fails (empty list means accepted)."""
    problems: list[str] = []
    if len(password) < MIN_PASSWORD_LENGTH:
        problems.append(f"at least {MIN_PASSWORD_LENGTH} characters")
    if not any(c.islower() for c in password):
        problems.append("at least one lower-case letter")
    if not any(c.isupper() for c in password):
        problems.append("at least one upper-case letter")
    if not any(c.isdigit() for c in password):
        problems.append("at least one digit")
    if not any(c in string.punctuation for c in password):
        problems.append("at least one special character")
    return problems


def generate_password(length: int = 20) -> str:
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*-_=+"
    return "".join(secrets.choice(alphabet) for _ in range(length))
