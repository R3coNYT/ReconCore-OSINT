"""Evidence: sources, findings, relationships, contradictions, timeline.

Core rule of the project: no information exists without provenance. Every
`Finding` points at a `Source`, which is itself timestamped and rated.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    Float,
    ForeignKey,
    Index,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import GUID, Base, JSONDict, TimestampMixin, TZDateTime, UUIDMixin
from app.models.enums import (
    FindingStatus,
    RelationshipType,
    SourceKind,
    VerificationStatus,
)


class Source(UUIDMixin, TimestampMixin, Base):
    """Where a piece of information came from: URL, tool output or manual entry."""

    __tablename__ = "sources"
    __table_args__ = (Index("ix_source_lookup", "investigation_id", "url"),)

    investigation_id: Mapped[uuid.UUID] = mapped_column(
        GUID,
        ForeignKey("investigations.id", ondelete="CASCADE"),
        index=True,
    )
    kind: Mapped[str] = mapped_column(String(40), default=SourceKind.TOOL_OUTPUT.value)
    url: Mapped[str | None] = mapped_column(String(1000))
    title: Mapped[str | None] = mapped_column(String(500))
    description: Mapped[str | None] = mapped_column(Text)
    plugin: Mapped[str | None] = mapped_column(String(60))
    plugin_run_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("plugin_runs.id", ondelete="SET NULL")
    )
    #: Reference to the retained raw data (payload key within the run).
    raw_reference: Mapped[str | None] = mapped_column(String(200))
    #: Source reliability rating (0..1), configurable per category.
    reliability: Mapped[float] = mapped_column(Float, default=0.5)
    date_discovered: Mapped[datetime | None] = mapped_column(TZDateTime)
    date_checked: Mapped[datetime | None] = mapped_column(TZDateTime)
    extra: Mapped[dict | None] = mapped_column(JSONDict)


class Finding(UUIDMixin, TimestampMixin, Base):
    """A discovered item. It stays a hypothesis until a human decides."""

    __tablename__ = "findings"
    __table_args__ = (
        Index("ix_finding_person_status", "person_id", "status"),
        Index("ix_finding_dedup", "person_id", "type", "dedup_key"),
    )

    investigation_id: Mapped[uuid.UUID] = mapped_column(
        GUID,
        ForeignKey("investigations.id", ondelete="CASCADE"),
        index=True,
    )
    person_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("persons.id", ondelete="CASCADE"), index=True
    )
    type: Mapped[str] = mapped_column(String(40), index=True)
    title: Mapped[str] = mapped_column(String(500))
    content: Mapped[dict | None] = mapped_column(JSONDict)
    #: Stable deduplication key computed during normalisation.
    dedup_key: Mapped[str | None] = mapped_column(String(300))
    source_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("sources.id", ondelete="SET NULL")
    )
    plugin: Mapped[str | None] = mapped_column(String(60))
    plugin_run_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("plugin_runs.id", ondelete="SET NULL")
    )
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    status: Mapped[str] = mapped_column(String(20), default=FindingStatus.NEW.value)
    discovered_at: Mapped[datetime | None] = mapped_column(TZDateTime)
    verified_at: Mapped[datetime | None] = mapped_column(TZDateTime)
    verified_by_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="SET NULL")
    )
    #: Score breakdown, readable by the analyst.
    score_explanation: Mapped[dict | None] = mapped_column(JSONDict)

    person = relationship("Person", back_populates="findings")
    source = relationship("Source")


class Relationship(UUIDMixin, TimestampMixin, Base):
    """Identity-graph edge. Nodes are referenced by (type, id)."""

    __tablename__ = "relationships"
    __table_args__ = (
        Index("ix_rel_src", "source_type", "source_ref"),
        Index("ix_rel_dst", "target_type", "target_ref"),
    )

    investigation_id: Mapped[uuid.UUID] = mapped_column(
        GUID,
        ForeignKey("investigations.id", ondelete="CASCADE"),
        index=True,
    )
    source_type: Mapped[str] = mapped_column(String(40))
    source_ref: Mapped[str] = mapped_column(String(64))
    target_type: Mapped[str] = mapped_column(String(40))
    target_ref: Mapped[str] = mapped_column(String(64))
    type: Mapped[str] = mapped_column(
        String(40), default=RelationshipType.RELATED_TO.value
    )
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    status: Mapped[str] = mapped_column(
        String(20), default=VerificationStatus.UNKNOWN.value
    )
    evidence_source_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("sources.id", ondelete="SET NULL")
    )
    note: Mapped[str | None] = mapped_column(Text)


class Contradiction(UUIDMixin, TimestampMixin, Base):
    """Two incompatible pieces of information. Never resolved automatically."""

    __tablename__ = "contradictions"

    investigation_id: Mapped[uuid.UUID] = mapped_column(
        GUID,
        ForeignKey("investigations.id", ondelete="CASCADE"),
        index=True,
    )
    person_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("persons.id", ondelete="CASCADE"), index=True
    )
    field: Mapped[str] = mapped_column(String(60))
    value_a: Mapped[str] = mapped_column(String(500))
    value_b: Mapped[str] = mapped_column(String(500))
    finding_a_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("findings.id", ondelete="SET NULL")
    )
    finding_b_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("findings.id", ondelete="SET NULL")
    )
    resolved: Mapped[bool] = mapped_column(Boolean, default=False)
    resolution: Mapped[str | None] = mapped_column(Text)
    resolved_value: Mapped[str | None] = mapped_column(String(500))
    resolved_by_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="SET NULL")
    )


class TimelineEvent(UUIDMixin, Base):
    """Chronological trail of the case file (searches, discoveries, decisions)."""

    __tablename__ = "timeline_events"
    __table_args__ = (Index("ix_timeline_scope", "investigation_id", "at"),)

    investigation_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("investigations.id", ondelete="CASCADE")
    )
    person_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("persons.id", ondelete="CASCADE"), index=True
    )
    at: Mapped[datetime] = mapped_column(TZDateTime, index=True)
    kind: Mapped[str] = mapped_column(String(50))
    message: Mapped[str] = mapped_column(String(600))
    actor: Mapped[str | None] = mapped_column(String(120))
    payload: Mapped[dict | None] = mapped_column(JSONDict)
