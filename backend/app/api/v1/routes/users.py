"""Account management (administrators only) and the audit log."""
from __future__ import annotations

import uuid
from datetime import UTC

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import db_session, require_admin
from app.models.user import AuditLog, RefreshToken, User
from app.schemas.auth import AuditLogOut, UserCreate, UserOut, UserUpdate
from app.schemas.common import Message
from app.security import audit
from app.security.passwords import hash_password

router = APIRouter(tags=["administration"])


@router.get("/users", response_model=list[UserOut])
def list_users(
    db: Session = Depends(db_session), _: User = Depends(require_admin)
) -> list[User]:
    return list(db.execute(select(User).order_by(User.created_at)).scalars().all())


@router.post("/users", response_model=UserOut, status_code=201)
def create_user(
    payload: UserCreate,
    request: Request,
    db: Session = Depends(db_session),
    admin: User = Depends(require_admin),
) -> User:
    exists = db.execute(
        select(User).where(User.email == payload.email.lower())
    ).scalar_one_or_none()
    if exists:
        raise HTTPException(status_code=409, detail="This email is already in use")

    user = User(
        email=payload.email.lower(),
        full_name=payload.full_name,
        hashed_password=hash_password(payload.password),
        role=payload.role.value,
    )
    db.add(user)
    db.flush()
    audit.record(
        db,
        action="user.created",
        user=admin,
        object_type="user",
        object_id=user.id,
        message=f"Created account {user.email} ({user.role})",
        request=request,
    )
    return user


@router.patch("/users/{user_id}", response_model=UserOut)
def update_user(
    user_id: uuid.UUID,
    payload: UserUpdate,
    request: Request,
    db: Session = Depends(db_session),
    admin: User = Depends(require_admin),
) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="Account not found")

    if payload.full_name is not None:
        user.full_name = payload.full_name
    if payload.role is not None:
        user.role = payload.role.value
    if payload.is_active is not None:
        user.is_active = payload.is_active
        if not payload.is_active:
            # Deactivation revokes every session immediately.
            from datetime import datetime

            now = datetime.now(UTC)
            user.tokens_valid_after = now
            for token in db.execute(
                select(RefreshToken).where(
                    RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None)
                )
            ).scalars().all():
                token.revoked_at = now

    audit.record(
        db,
        action="user.updated",
        user=admin,
        object_type="user",
        object_id=user.id,
        detail=payload.model_dump(exclude_none=True),
        request=request,
    )
    return user


@router.delete("/users/{user_id}", response_model=Message)
def delete_user(
    user_id: uuid.UUID,
    request: Request,
    db: Session = Depends(db_session),
    admin: User = Depends(require_admin),
) -> Message:
    if user_id == admin.id:
        raise HTTPException(status_code=400, detail="You cannot delete your own account")
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="Account not found")
    email = user.email
    db.delete(user)
    audit.record(
        db,
        action="user.deleted",
        user=admin,
        object_type="user",
        object_id=user_id,
        message=f"Deleted account {email}",
        request=request,
    )
    return Message(detail="Account deleted")


@router.get("/audit-logs", response_model=list[AuditLogOut])
def list_audit_logs(
    db: Session = Depends(db_session),
    _: User = Depends(require_admin),
    action: str | None = None,
    object_type: str | None = None,
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> list[AuditLog]:
    query = select(AuditLog).order_by(AuditLog.at.desc())
    if action:
        query = query.where(AuditLog.action == action)
    if object_type:
        query = query.where(AuditLog.object_type == object_type)
    return list(db.execute(query.limit(limit).offset(offset)).scalars().all())
