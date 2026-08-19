"""Schemas for identifiers, usernames, platforms and social profiles."""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.models.enums import IdentifierType, PlatformCategory, VerificationStatus
from app.schemas.common import ORMModel, ScoreOut


class IdentifierCreate(BaseModel):
    type: IdentifierType
    value: str = Field(min_length=1, max_length=500)
    platform: str | None = Field(
        default=None, description="Platform name when relevant (e.g. Instagram)."
    )
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    status: VerificationStatus = VerificationStatus.UNKNOWN
    is_former: bool = False
    note: str | None = None
    source_url: str | None = Field(
        default=None, description="Provenance URL. Creates a manual source."
    )


class IdentifierUpdate(BaseModel):
    value: str | None = None
    status: VerificationStatus | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    is_former: bool | None = None
    note: str | None = None
    platform: str | None = None


class IdentifierOut(ORMModel):
    id: uuid.UUID
    person_id: uuid.UUID | None = None
    type: str
    value: str
    normalized_value: str
    platform_id: uuid.UUID | None = None
    confidence: float
    status: str
    is_former: bool
    source_id: uuid.UUID | None = None
    note: str | None = None
    created_at: datetime


class IdentifierCreated(BaseModel):
    """Creation response: includes the plugins that can act on this value."""

    identifier: IdentifierOut
    created: bool
    compatible_plugins: list[dict]


class UsernameCreate(BaseModel):
    value: str = Field(min_length=1, max_length=200)
    platform: str | None = None
    url: str | None = None
    confidence: float = Field(default=0.4, ge=0.0, le=1.0)
    status: VerificationStatus = VerificationStatus.UNKNOWN
    note: str | None = None


class UsernameUpdate(BaseModel):
    platform: str | None = None
    url: str | None = None
    status: VerificationStatus | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    note: str | None = None


class UsernameOut(ORMModel):
    id: uuid.UUID
    person_id: uuid.UUID
    value: str
    normalized_value: str
    platform_id: uuid.UUID | None = None
    url: str | None = None
    status: str
    confidence: float
    is_variant: bool
    variant_of_id: uuid.UUID | None = None
    variant_rule: str | None = None
    discovered_at: datetime | None = None
    note: str | None = None


class VariantSuggestion(BaseModel):
    value: str
    rule: str
    confidence: float
    status: str = "HYPOTHESIS"


class VariantResponse(BaseModel):
    suggestions: list[VariantSuggestion]
    warning: str = (
        "Variants are search hypotheses. They do not automatically belong to the "
        "person and must be validated."
    )


class VariantSaveRequest(BaseModel):
    values: list[str] = Field(min_length=1)


class PlatformOut(ORMModel):
    id: uuid.UUID
    name: str
    slug: str
    category: str
    base_url: str | None = None
    profile_url_template: str | None = None
    icon: str | None = None
    enabled: bool


class PlatformCreate(BaseModel):
    name: str
    category: PlatformCategory = PlatformCategory.OTHER
    base_url: str | None = None
    profile_url_template: str | None = None
    icon: str | None = None
    enabled: bool = True


class SocialProfileCreate(BaseModel):
    platform: str
    username: str
    url: str | None = None
    status: VerificationStatus = VerificationStatus.UNKNOWN
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    display_name: str | None = None
    bio: str | None = None
    location: str | None = None
    external_url: str | None = None
    note: str | None = None


class SocialProfileOut(ORMModel):
    id: uuid.UUID
    person_id: uuid.UUID
    platform_id: uuid.UUID | None = None
    username: str
    url: str | None = None
    status: str
    confidence: float
    display_name: str | None = None
    bio: str | None = None
    avatar_url: str | None = None
    external_url: str | None = None
    location: str | None = None
    public_email: str | None = None
    public_phone: str | None = None
    followers: int | None = None
    following: int | None = None
    posts_count: int | None = None
    is_verified: bool | None = None
    is_private: bool | None = None
    is_business: bool | None = None
    platform_user_id: str | None = None
    discovered_by_plugin: str | None = None
    last_checked_at: datetime | None = None


class SocialProfileDetail(SocialProfileOut):
    score: ScoreOut


class StatusDecision(BaseModel):
    status: VerificationStatus
    reason: str | None = None
