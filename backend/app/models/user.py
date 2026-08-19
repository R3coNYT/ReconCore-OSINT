"""Users, refresh tokens and the audit log."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import GUID, Base, JSONDict, TimestampMixin, TZDateTime, UUIDMixin
from app.models.enums import UserRole


class User(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    full_name: Mapped[str | None] = mapped_column(String(200))
    hashed_password: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20), default=UserRole.ANALYST.value)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_login_at: Mapped[datetime | None] = mapped_column(TZDateTime)
    #: Invalidates every token issued before this date (global logout, reset).
    tokens_valid_after: Mapped[datetime | None] = mapped_column(TZDateTime)

    investigations = relationship(
        "Investigation", back_populates="owner", foreign_keys="Investigation.owner_id"
    )


class RefreshToken(UUIDMixin, TimestampMixin, Base):
    """Refresh token, stored hashed and individually revocable."""

    __tablename__ = "refresh_tokens"

    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(TZDateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(TZDateTime)
    user_agent: Mapped[str | None] = mapped_column(String(300))
    ip_address: Mapped[str | None] = mapped_column(String(64))


class AuditLog(UUIDMixin, Base):
    """Append-only log: who did what, to which object, and when."""

    __tablename__ = "audit_logs"
    __table_args__ = (Index("ix_audit_object", "object_type", "object_id"),)

    at: Mapped[datetime] = mapped_column(
        TZDateTime, index=True, nullable=False
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    user_email: Mapped[str | None] = mapped_column(String(320))
    action: Mapped[str] = mapped_column(String(80), index=True)
    object_type: Mapped[str | None] = mapped_column(String(60))
    object_id: Mapped[str | None] = mapped_column(String(64))
    ip_address: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(300))
    detail: Mapped[dict | None] = mapped_column(JSONDict)
    message: Mapped[str | None] = mapped_column(Text)
