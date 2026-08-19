"""Audit-log writer. Called by every route that changes state."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import Request
from sqlalchemy.orm import Session

from app.models.user import AuditLog, User


def record(
    db: Session,
    *,
    action: str,
    user: User | None = None,
    object_type: str | None = None,
    object_id: Any = None,
    message: str | None = None,
    detail: dict | None = None,
    request: Request | None = None,
) -> AuditLog:
    entry = AuditLog(
        at=datetime.now(UTC),
        user_id=user.id if user else None,
        user_email=user.email if user else None,
        action=action,
        object_type=object_type,
        object_id=str(object_id) if object_id is not None else None,
        message=message,
        detail=detail,
        ip_address=_client_ip(request),
        user_agent=(request.headers.get("user-agent") if request else None),
    )
    db.add(entry)
    return entry


def _client_ip(request: Request | None) -> str | None:
    if request is None:
        return None
    # Behind Nginx, X-Forwarded-For holds the real client IP in first position.
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()[:64]
    return request.client.host if request.client else None
