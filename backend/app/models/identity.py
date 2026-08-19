"""Identifiers, usernames, platforms and social profiles."""
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
from app.models.enums import PlatformCategory, VerificationStatus


class Platform(UUIDMixin, TimestampMixin, Base):
    """A platform (social network, forum, code forge...). A first-class entity."""

    __tablename__ = "platforms"

    name: Mapped[str] = mapped_column(String(120))
    slug: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    category: Mapped[str] = mapped_column(
        String(40), default=PlatformCategory.OTHER.value
    )
    base_url: Mapped[str | None] = mapped_column(String(300))
    #: Profile URL template, e.g. "https://github.com/{username}".
    profile_url_template: Mapped[str | None] = mapped_column(String(400))
    icon: Mapped[str | None] = mapped_column(String(80))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    def profile_url(self, username: str) -> str | None:
        if self.profile_url_template:
            return self.profile_url_template.format(username=username)
        return None


class Identifier(UUIDMixin, TimestampMixin, Base):
    """Generic identifier attached to a person or an organisation."""

    __tablename__ = "identifiers"
    __table_args__ = (
        Index("ix_identifier_lookup", "type", "normalized_value"),
        UniqueConstraint(
            "person_id", "type", "normalized_value", "platform_id",
            name="uq_identifier_person_value",
        ),
    )

    investigation_id: Mapped[uuid.UUID] = mapped_column(
        GUID,
        ForeignKey("investigations.id", ondelete="CASCADE"),
        index=True,
    )
    person_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("persons.id", ondelete="CASCADE"), index=True
    )
    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("organizations.id", ondelete="CASCADE")
    )
    type: Mapped[str] = mapped_column(String(30), index=True)
    value: Mapped[str] = mapped_column(String(500))
    #: Canonical form used for comparison and deduplication.
    normalized_value: Mapped[str] = mapped_column(String(500), index=True)
    platform_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("platforms.id", ondelete="SET NULL")
    )
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    status: Mapped[str] = mapped_column(
        String(20), default=VerificationStatus.UNKNOWN.value
    )
    #: Former contact detail (previous email, previous address...).
    is_former: Mapped[bool] = mapped_column(Boolean, default=False)
    source_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("sources.id", ondelete="SET NULL")
    )
    note: Mapped[str | None] = mapped_column(Text)
    extra: Mapped[dict | None] = mapped_column(JSONDict)

    person = relationship("Person", back_populates="identifiers")
    platform = relationship("Platform")
    source = relationship("Source")


class Username(UUIDMixin, TimestampMixin, Base):
    """A username. May exist with no known platform yet (still to be searched)."""

    __tablename__ = "usernames"
    __table_args__ = (
        Index("ix_username_lookup", "normalized_value"),
        UniqueConstraint(
            "person_id", "normalized_value", "platform_id", name="uq_username_person"
        ),
    )

    investigation_id: Mapped[uuid.UUID] = mapped_column(
        GUID,
        ForeignKey("investigations.id", ondelete="CASCADE"),
        index=True,
    )
    person_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("persons.id", ondelete="CASCADE"), index=True
    )
    value: Mapped[str] = mapped_column(String(200))
    normalized_value: Mapped[str] = mapped_column(String(200), index=True)
    platform_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("platforms.id", ondelete="SET NULL")
    )
    url: Mapped[str | None] = mapped_column(String(600))
    status: Mapped[str] = mapped_column(
        String(20), default=VerificationStatus.UNKNOWN.value
    )
    confidence: Mapped[float] = mapped_column(Float, default=0.4)
    #: True when produced by the variant engine: a hypothesis, never an identity.
    is_variant: Mapped[bool] = mapped_column(Boolean, default=False)
    variant_of_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("usernames.id", ondelete="SET NULL")
    )
    #: Rule that produced the variant (e.g. "dot_separator").
    variant_rule: Mapped[str | None] = mapped_column(String(60))
    source_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("sources.id", ondelete="SET NULL")
    )
    discovered_at: Mapped[datetime | None] = mapped_column(TZDateTime)
    note: Mapped[str | None] = mapped_column(Text)

    person = relationship("Person", back_populates="usernames")
    platform = relationship("Platform")
    source = relationship("Source")


class SocialProfile(UUIDMixin, TimestampMixin, Base):
    """Profile found on a platform. Presence is not identity."""

    __tablename__ = "social_profiles"
    __table_args__ = (
        UniqueConstraint("person_id", "platform_id", "username", name="uq_profile"),
    )

    investigation_id: Mapped[uuid.UUID] = mapped_column(
        GUID,
        ForeignKey("investigations.id", ondelete="CASCADE"),
        index=True,
    )
    person_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("persons.id", ondelete="CASCADE"), index=True
    )
    platform_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("platforms.id", ondelete="SET NULL")
    )
    username: Mapped[str] = mapped_column(String(200))
    url: Mapped[str | None] = mapped_column(String(600))
    status: Mapped[str] = mapped_column(
        String(20), default=VerificationStatus.UNKNOWN.value
    )
    confidence: Mapped[float] = mapped_column(Float, default=0.4)

    # --- Observed public metadata (feeds the correlation engine) ---
    display_name: Mapped[str | None] = mapped_column(String(250))
    bio: Mapped[str | None] = mapped_column(Text)
    avatar_url: Mapped[str | None] = mapped_column(String(600))
    external_url: Mapped[str | None] = mapped_column(String(600))
    location: Mapped[str | None] = mapped_column(String(200))
    public_email: Mapped[str | None] = mapped_column(String(320))
    public_phone: Mapped[str | None] = mapped_column(String(60))
    followers: Mapped[int | None] = mapped_column(Integer)
    following: Mapped[int | None] = mapped_column(Integer)
    posts_count: Mapped[int | None] = mapped_column(Integer)
    is_verified: Mapped[bool | None] = mapped_column(Boolean)
    is_private: Mapped[bool | None] = mapped_column(Boolean)
    is_business: Mapped[bool | None] = mapped_column(Boolean)
    platform_user_id: Mapped[str | None] = mapped_column(String(120))
    raw: Mapped[dict | None] = mapped_column(JSONDict)

    source_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("sources.id", ondelete="SET NULL")
    )
    discovered_by_plugin: Mapped[str | None] = mapped_column(String(60))
    last_checked_at: Mapped[datetime | None] = mapped_column(TZDateTime)

    person = relationship("Person", back_populates="social_profiles")
    platform = relationship("Platform")
    source = relationship("Source")
