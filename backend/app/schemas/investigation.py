"""Schemas for case files, people, organisations, notes and tags."""
from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import BaseModel, Field

from app.models.enums import EntityType
from app.schemas.common import ORMModel, ScoreOut


class InvestigationCreate(BaseModel):
    title: str = Field(min_length=1, max_length=250)
    entity_type: EntityType = EntityType.PERSON
    description: str | None = None
    legal_basis: str | None = Field(
        default=None,
        description="Purpose / legal basis of the investigation (internal traceability).",
    )
    automation_enabled: bool = True
    default_depth: int = Field(default=1, ge=1, le=4)
    retention_until: datetime | None = None


class InvestigationUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    legal_basis: str | None = None
    status: str | None = None
    automation_enabled: bool | None = None
    default_depth: int | None = Field(default=None, ge=1, le=4)
    retention_until: datetime | None = None


class InvestigationOut(ORMModel):
    id: uuid.UUID
    title: str
    entity_type: str
    description: str | None = None
    legal_basis: str | None = None
    status: str
    owner_id: uuid.UUID | None = None
    automation_enabled: bool
    default_depth: int
    retention_until: datetime | None = None
    last_activity_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class InvestigationStats(BaseModel):
    persons: int
    identifiers: int
    usernames: int
    social_profiles: int
    findings: int
    sources: int
    relationships: int
    searches: int
    open_contradictions: int
    last_search_at: datetime | None = None


class InvestigationDetail(InvestigationOut):
    stats: InvestigationStats


class PersonCreate(BaseModel):
    display_name: str = Field(min_length=1, max_length=250)
    first_name: str | None = None
    last_name: str | None = None
    full_name: str | None = None
    date_of_birth: date | None = None
    profession: str | None = None
    summary: str | None = None


class PersonUpdate(BaseModel):
    display_name: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    full_name: str | None = None
    date_of_birth: date | None = None
    profession: str | None = None
    summary: str | None = None
    is_archived: bool | None = None


class TagOut(ORMModel):
    id: uuid.UUID
    name: str
    slug: str
    color: str | None = None


class PersonOut(ORMModel):
    id: uuid.UUID
    investigation_id: uuid.UUID
    display_name: str
    first_name: str | None = None
    last_name: str | None = None
    full_name: str | None = None
    date_of_birth: date | None = None
    profession: str | None = None
    summary: str | None = None
    confidence_score: float
    is_archived: bool
    last_search_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    tags: list[TagOut] = []


class PersonCounters(BaseModel):
    identifiers: int
    usernames: int
    social_profiles: int
    findings: int
    sources: int
    relationships: int
    searches: int
    open_contradictions: int
    new_findings: int


class PersonDetail(PersonOut):
    counters: PersonCounters
    score: ScoreOut


class OrganizationCreate(BaseModel):
    name: str
    legal_name: str | None = None
    registration_id: str | None = None
    country: str | None = None
    website: str | None = None
    summary: str | None = None


class OrganizationOut(ORMModel):
    id: uuid.UUID
    investigation_id: uuid.UUID
    name: str
    legal_name: str | None = None
    registration_id: str | None = None
    country: str | None = None
    website: str | None = None
    summary: str | None = None
    created_at: datetime


class NoteCreate(BaseModel):
    title: str | None = None
    body: str = Field(min_length=1)
    person_id: uuid.UUID | None = None


class NoteOut(ORMModel):
    id: uuid.UUID
    investigation_id: uuid.UUID
    person_id: uuid.UUID | None = None
    title: str | None = None
    body: str
    created_at: datetime


class TagAssign(BaseModel):
    name: str
    color: str | None = None
