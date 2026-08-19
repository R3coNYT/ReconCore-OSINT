"""Declarative base, portable column types and mixins shared by all models.

The column types are declared portably:
  * `GUID`       -> native `UUID` on PostgreSQL, `CHAR(32)` elsewhere;
  * `JSONDict`   -> `JSONB` on PostgreSQL, `JSON` elsewhere;
  * `TZDateTime` -> always timezone-aware UTC values.

PostgreSQL remains the production target (indexable JSONB, native UUID); the
portability also lets the integration suite run on SQLite with no infrastructure.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import JSON, TypeDecorator

#: Technical identifier: native UUID on PostgreSQL.
GUID = Uuid(as_uuid=True)

#: JSON document: JSONB (indexable, queryable) on PostgreSQL.
JSONDict = JSON().with_variant(JSONB(), "postgresql")


class TZDateTime(TypeDecorator):
    """Timestamp that is always timezone-aware, in UTC.

    PostgreSQL naturally returns timezone-aware datetimes; some backends
    (SQLite) return naive ones. This decorator guarantees that a value read back
    is always comparable with `datetime.now(UTC)`, which removes the
    "can't compare offset-naive and offset-aware datetimes" class of bug on
    token expiry and retention comparisons.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    """Shared declarative base."""


class UUIDMixin:
    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        TZDateTime,
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
