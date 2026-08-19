"""Authentication routes: login, refresh, logout, profile."""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import db_session, get_current_user
from app.models.user import RefreshToken, User
from app.schemas.auth import (
    LoginRequest,
    PasswordChange,
    RefreshRequest,
    TokenPair,
    UserOut,
)
from app.schemas.common import Message
from app.security import audit
from app.security.passwords import hash_password, needs_rehash, verify_password
from app.security.ratelimit import login_limiter
from app.security.tokens import (
    create_access_token,
    create_refresh_token,
    hash_refresh_token,
)

router = APIRouter(prefix="/auth", tags=["auth"])


def _now() -> datetime:
    return datetime.now(UTC)


@router.post("/login", response_model=TokenPair)
def login(
    payload: LoginRequest, request: Request, db: Session = Depends(db_session)
) -> TokenPair:
    identity = f"{payload.email.lower()}|{audit._client_ip(request) or '-'}"
    if not login_limiter.allow(identity):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many attempts. Try again later.",
            headers={"Retry-After": str(login_limiter.retry_after())},
        )

    user = db.execute(
        select(User).where(User.email == payload.email.lower())
    ).scalar_one_or_none()

    # Identical message on every failure: no account enumeration.
    if user is None or not verify_password(payload.password, user.hashed_password):
        audit.record(
            db,
            action="auth.login_failed",
            message=f"Failed login attempt for {payload.email}",
            request=request,
        )
        raise HTTPException(status_code=401, detail="Invalid credentials")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account deactivated")

    if needs_rehash(user.hashed_password):
        user.hashed_password = hash_password(payload.password)

    user.last_login_at = _now()
    access_token, expires_at = create_access_token(user.id, user.role)
    raw_refresh, token_hash, refresh_expires = create_refresh_token()
    db.add(
        RefreshToken(
            user_id=user.id,
            token_hash=token_hash,
            expires_at=refresh_expires,
            user_agent=request.headers.get("user-agent", "")[:300],
            ip_address=audit._client_ip(request),
        )
    )
    audit.record(db, action="auth.login", user=user, request=request)
    return TokenPair(
        access_token=access_token, refresh_token=raw_refresh, expires_at=expires_at
    )


@router.post("/refresh", response_model=TokenPair)
def refresh(payload: RefreshRequest, request: Request, db: Session = Depends(db_session)) -> TokenPair:
    token_hash = hash_refresh_token(payload.refresh_token)
    record = db.execute(
        select(RefreshToken).where(RefreshToken.token_hash == token_hash)
    ).scalar_one_or_none()

    if record is None or record.revoked_at is not None or record.expires_at < _now():
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    user = db.get(User, record.user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="Unknown or deactivated account")

    # Rotation: the previous token is revoked immediately.
    record.revoked_at = _now()
    access_token, expires_at = create_access_token(user.id, user.role)
    raw_refresh, new_hash, refresh_expires = create_refresh_token()
    db.add(
        RefreshToken(
            user_id=user.id,
            token_hash=new_hash,
            expires_at=refresh_expires,
            user_agent=request.headers.get("user-agent", "")[:300],
            ip_address=audit._client_ip(request),
        )
    )
    return TokenPair(
        access_token=access_token, refresh_token=raw_refresh, expires_at=expires_at
    )


@router.post("/logout", response_model=Message)
def logout(
    payload: RefreshRequest,
    request: Request,
    db: Session = Depends(db_session),
    user: User = Depends(get_current_user),
) -> Message:
    record = db.execute(
        select(RefreshToken).where(
            RefreshToken.token_hash == hash_refresh_token(payload.refresh_token)
        )
    ).scalar_one_or_none()
    if record is not None:
        record.revoked_at = _now()
    audit.record(db, action="auth.logout", user=user, request=request)
    return Message(detail="Session ended")


@router.post("/logout-all", response_model=Message)
def logout_all(
    request: Request,
    db: Session = Depends(db_session),
    user: User = Depends(get_current_user),
) -> Message:
    """Revoke every session and invalidate all outstanding access tokens."""
    for token in db.execute(
        select(RefreshToken).where(
            RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None)
        )
    ).scalars().all():
        token.revoked_at = _now()
    user.tokens_valid_after = _now()
    audit.record(db, action="auth.logout_all", user=user, request=request)
    return Message(detail="All sessions have been revoked")


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)) -> User:
    return user


@router.post("/password", response_model=Message)
def change_password(
    payload: PasswordChange,
    request: Request,
    db: Session = Depends(db_session),
    user: User = Depends(get_current_user),
) -> Message:
    if not verify_password(payload.current_password, user.hashed_password):
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    user.hashed_password = hash_password(payload.new_password)
    user.tokens_valid_after = _now()
    for token in db.execute(
        select(RefreshToken).where(
            RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None)
        )
    ).scalars().all():
        token.revoked_at = _now()
    audit.record(db, action="auth.password_changed", user=user, request=request)
    return Message(detail="Password changed. All sessions have been revoked.")
