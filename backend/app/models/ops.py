"""Operations: plugin registry, encrypted secrets, searches and runs."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import GUID, Base, JSONDict, TimestampMixin, TZDateTime, UUIDMixin
from app.models.enums import RiskLevel, RunStatus


class PluginRegistryEntry(UUIDMixin, TimestampMixin, Base):
    """Persistent plugin state: activation, quotas, last audit."""

    __tablename__ = "plugins"

    name: Mapped[str] = mapped_column(String(60), unique=True, index=True)
    version: Mapped[str] = mapped_column(String(30), default="0.0.0")
    description: Mapped[str | None] = mapped_column(Text)
    repository: Mapped[str | None] = mapped_column(String(400))
    license: Mapped[str | None] = mapped_column(String(80))
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    supported_identifiers: Mapped[list | None] = mapped_column(JSONDict)
    requires_secrets: Mapped[list | None] = mapped_column(JSONDict)

    # --- Usage limits (respect for the services being queried) ---
    requests_per_minute: Mapped[int] = mapped_column(Integer, default=30)
    concurrency: Mapped[int] = mapped_column(Integer, default=2)
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=300)
    retry_count: Mapped[int] = mapped_column(Integer, default=1)

    # --- Security ---
    risk_level: Mapped[str] = mapped_column(String(20), default=RiskLevel.UNKNOWN.value)
    last_audit_at: Mapped[datetime | None] = mapped_column(TZDateTime)
    audit_report: Mapped[dict | None] = mapped_column(JSONDict)
    health_status: Mapped[str | None] = mapped_column(String(20))
    health_message: Mapped[str | None] = mapped_column(Text)
    health_checked_at: Mapped[datetime | None] = mapped_column(TZDateTime)


class PluginSecret(UUIDMixin, TimestampMixin, Base):
    """Plugin secret, encrypted at rest (Fernet). Never exposed by the API."""

    __tablename__ = "plugin_secrets"
    __table_args__ = (UniqueConstraint("plugin", "key", name="uq_plugin_secret"),)

    plugin: Mapped[str] = mapped_column(String(60), index=True)
    key: Mapped[str] = mapped_column(String(60))
    ciphertext: Mapped[str] = mapped_column(Text)
    hint: Mapped[str | None] = mapped_column(String(120))
    set_by_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="SET NULL")
    )


class Search(UUIDMixin, TimestampMixin, Base):
    """A search campaign: one target, one depth, N plugin runs."""

    __tablename__ = "searches"

    investigation_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID,
        ForeignKey("investigations.id", ondelete="CASCADE"),
        index=True,
    )
    person_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("persons.id", ondelete="CASCADE"), index=True
    )
    initiated_by_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="SET NULL")
    )
    label: Mapped[str | None] = mapped_column(String(250))
    target_type: Mapped[str] = mapped_column(String(30))
    target_value: Mapped[str] = mapped_column(String(500))
    depth: Mapped[int] = mapped_column(Integer, default=1)
    #: Only re-runs the plugins affected by newly added identifiers.
    differential: Mapped[bool] = mapped_column(Boolean, default=True)
    params: Mapped[dict | None] = mapped_column(JSONDict)
    status: Mapped[str] = mapped_column(String(20), default=RunStatus.PENDING.value)
    started_at: Mapped[datetime | None] = mapped_column(TZDateTime)
    finished_at: Mapped[datetime | None] = mapped_column(TZDateTime)
    stats: Mapped[dict | None] = mapped_column(JSONDict)

    runs = relationship(
        "PluginRun", back_populates="search", cascade="all, delete-orphan"
    )


class PluginRun(UUIDMixin, TimestampMixin, Base):
    """A single plugin run against one target, with logs and retained raw output."""

    __tablename__ = "plugin_runs"
    __table_args__ = (
        Index("ix_run_target", "plugin", "target_type", "normalized_target"),
    )

    search_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("searches.id", ondelete="CASCADE"), index=True
    )
    investigation_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("investigations.id", ondelete="CASCADE")
    )
    person_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("persons.id", ondelete="CASCADE")
    )
    plugin: Mapped[str] = mapped_column(String(60), index=True)
    plugin_version: Mapped[str | None] = mapped_column(String(30))
    target_type: Mapped[str] = mapped_column(String(30))
    target_value: Mapped[str] = mapped_column(String(500))
    normalized_target: Mapped[str] = mapped_column(String(500))
    depth: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(20), default=RunStatus.PENDING.value)
    celery_task_id: Mapped[str | None] = mapped_column(String(80))
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    started_at: Mapped[datetime | None] = mapped_column(TZDateTime)
    finished_at: Mapped[datetime | None] = mapped_column(TZDateTime)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    #: Raw output returned by the plugin (retained for reprocessing).
    raw_output: Mapped[dict | None] = mapped_column(JSONDict)
    logs: Mapped[list | None] = mapped_column(JSONDict)
    error: Mapped[str | None] = mapped_column(Text)
    items_found: Mapped[int] = mapped_column(Integer, default=0)

    search = relationship("Search", back_populates="runs")
    results = relationship(
        "SearchResult", back_populates="run", cascade="all, delete-orphan"
    )


class SearchResult(UUIDMixin, TimestampMixin, Base):
    """Normalised item from a run, before and after becoming a Finding."""

    __tablename__ = "search_results"

    run_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("plugin_runs.id", ondelete="CASCADE"),
        index=True,
    )
    kind: Mapped[str] = mapped_column(String(40))
    payload: Mapped[dict] = mapped_column(JSONDict)
    dedup_key: Mapped[str | None] = mapped_column(String(300), index=True)
    finding_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("findings.id", ondelete="SET NULL")
    )
    is_duplicate: Mapped[bool] = mapped_column(Boolean, default=False)

    run = relationship("PluginRun", back_populates="results")
