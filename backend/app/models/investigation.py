"""Investigation case files and their main entities (people, organisations)."""
from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    Float,
    ForeignKey,
    Index,
    String,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import GUID, Base, JSONDict, TimestampMixin, TZDateTime, UUIDMixin
from app.models.enums import EntityType

person_tags = Table(
    "person_tags",
    Base.metadata,
    Column(
        "person_id",
        GUID,
        ForeignKey("persons.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "tag_id",
        GUID,
        ForeignKey("tags.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)


class Investigation(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "investigations"

    title: Mapped[str] = mapped_column(String(250), index=True)
    entity_type: Mapped[str] = mapped_column(String(30), default=EntityType.PERSON.value)
    description: Mapped[str | None] = mapped_column(Text)
    #: Legal basis / purpose of the investigation. Documents why data is collected.
    legal_basis: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="OPEN")
    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    automation_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    default_depth: Mapped[int] = mapped_column(default=1)
    retention_until: Mapped[datetime | None] = mapped_column(TZDateTime)
    last_activity_at: Mapped[datetime | None] = mapped_column(TZDateTime)

    owner = relationship("User", back_populates="investigations", foreign_keys=[owner_id])
    persons = relationship(
        "Person", back_populates="investigation", cascade="all, delete-orphan"
    )
    organizations = relationship(
        "Organization", back_populates="investigation", cascade="all, delete-orphan"
    )


class Person(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "persons"

    investigation_id: Mapped[uuid.UUID] = mapped_column(
        GUID,
        ForeignKey("investigations.id", ondelete="CASCADE"),
        index=True,
    )
    display_name: Mapped[str] = mapped_column(String(250), index=True)
    first_name: Mapped[str | None] = mapped_column(String(120))
    last_name: Mapped[str | None] = mapped_column(String(120))
    full_name: Mapped[str | None] = mapped_column(String(250))
    date_of_birth: Mapped[date | None] = mapped_column(Date)
    profession: Mapped[str | None] = mapped_column(String(200))
    summary: Mapped[str | None] = mapped_column(Text)
    #: Overall case-file consolidation score (0..1), recomputed by the scorer.
    confidence_score: Mapped[float] = mapped_column(Float, default=0.0)
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False)
    last_search_at: Mapped[datetime | None] = mapped_column(TZDateTime)

    investigation = relationship("Investigation", back_populates="persons")
    identifiers = relationship(
        "Identifier", back_populates="person", cascade="all, delete-orphan"
    )
    usernames = relationship(
        "Username", back_populates="person", cascade="all, delete-orphan"
    )
    social_profiles = relationship(
        "SocialProfile", back_populates="person", cascade="all, delete-orphan"
    )
    findings = relationship(
        "Finding", back_populates="person", cascade="all, delete-orphan"
    )
    tags = relationship("Tag", secondary=person_tags, back_populates="persons")


class Organization(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "organizations"

    investigation_id: Mapped[uuid.UUID] = mapped_column(
        GUID,
        ForeignKey("investigations.id", ondelete="CASCADE"),
        index=True,
    )
    name: Mapped[str] = mapped_column(String(250), index=True)
    legal_name: Mapped[str | None] = mapped_column(String(250))
    registration_id: Mapped[str | None] = mapped_column(String(120))
    country: Mapped[str | None] = mapped_column(String(80))
    website: Mapped[str | None] = mapped_column(String(500))
    summary: Mapped[str | None] = mapped_column(Text)
    extra: Mapped[dict | None] = mapped_column(JSONDict)

    investigation = relationship("Investigation", back_populates="organizations")


class Tag(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "tags"
    __table_args__ = (UniqueConstraint("slug", name="uq_tag_slug"),)

    name: Mapped[str] = mapped_column(String(80))
    slug: Mapped[str] = mapped_column(String(80), index=True)
    color: Mapped[str | None] = mapped_column(String(20))

    persons = relationship("Person", secondary=person_tags, back_populates="tags")


class Note(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "notes"
    __table_args__ = (Index("ix_notes_scope", "investigation_id", "person_id"),)

    investigation_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("investigations.id", ondelete="CASCADE")
    )
    person_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("persons.id", ondelete="CASCADE")
    )
    author_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="SET NULL")
    )
    title: Mapped[str | None] = mapped_column(String(250))
    body: Mapped[str] = mapped_column(Text)
