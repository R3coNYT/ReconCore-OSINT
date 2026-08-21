"""Schemas for findings, sources, relationships, contradictions and searches."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.models.enums import FindingStatus, IdentifierType, SourceKind
from app.schemas.common import ORMModel


class FindingOut(ORMModel):
    id: uuid.UUID
    person_id: uuid.UUID | None = None
    type: str
    title: str
    content: dict | None = None
    status: str
    confidence: float
    plugin: str | None = None
    source_id: uuid.UUID | None = None
    discovered_at: datetime | None = None
    verified_at: datetime | None = None
    score_explanation: dict | None = None


class FindingDecision(BaseModel):
    decision: Literal["confirm", "reject", "investigate", "probable", "outdated"]
    reason: str | None = None


class FindingUpdate(BaseModel):
    status: FindingStatus | None = None
    title: str | None = None


class SourceOut(ORMModel):
    id: uuid.UUID
    kind: str
    url: str | None = None
    title: str | None = None
    description: str | None = None
    plugin: str | None = None
    raw_reference: str | None = None
    reliability: float
    date_discovered: datetime | None = None
    date_checked: datetime | None = None


class SourceCreate(BaseModel):
    kind: SourceKind = SourceKind.MANUAL_ENTRY
    url: str | None = None
    title: str | None = None
    description: str | None = None
    reliability: float = Field(default=0.6, ge=0.0, le=1.0)


class RelationshipOut(ORMModel):
    id: uuid.UUID
    source_type: str
    source_ref: str
    target_type: str
    target_ref: str
    type: str
    confidence: float
    status: str
    note: str | None = None


class ContradictionOut(ORMModel):
    id: uuid.UUID
    person_id: uuid.UUID | None = None
    field: str
    value_a: str
    value_b: str
    resolved: bool
    resolution: str | None = None
    resolved_value: str | None = None
    created_at: datetime


class ContradictionResolve(BaseModel):
    resolved_value: str
    resolution: str | None = None


class SearchCreate(BaseModel):
    target_type: IdentifierType
    target_value: str = Field(min_length=1, max_length=500)
    person_id: uuid.UUID | None = None
    investigation_id: uuid.UUID | None = None
    depth: int = Field(default=1, ge=1, le=4)
    differential: bool = True
    plugins: list[str] | None = Field(
        default=None, description="Restrict to these plugins (default: every compatible one)."
    )
    force: bool = Field(
        default=False, description="Ignore differential search and re-run everything."
    )
    options: dict = Field(default_factory=dict)


class SearchOut(ORMModel):
    id: uuid.UUID
    investigation_id: uuid.UUID | None = None
    person_id: uuid.UUID | None = None
    label: str | None = None
    target_type: str
    target_value: str
    depth: int
    differential: bool
    status: str
    started_at: datetime | None = None
    finished_at: datetime | None = None
    stats: dict | None = None
    created_at: datetime


class PluginRunOut(ORMModel):
    id: uuid.UUID
    search_id: uuid.UUID | None = None
    plugin: str
    plugin_version: str | None = None
    target_type: str
    target_value: str
    depth: int
    status: str
    progress: float
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_ms: int | None = None
    items_found: int
    error: str | None = None
    logs: list | None = None


class SearchDetail(SearchOut):
    runs: list[PluginRunOut]


class SearchProgress(BaseModel):
    search_id: uuid.UUID
    status: str
    total_runs: int
    finished_runs: int
    progress: float
    runs: list[PluginRunOut]


class GraphNode(BaseModel):
    id: str
    type: str
    label: str
    ref: str
    confidence: float | None = None
    status: str | None = None
    subtype: str | None = None
    url: str | None = None
    category: str | None = None
    is_variant: bool | None = None


class GraphEdge(BaseModel):
    id: str
    source: str
    target: str
    type: str
    confidence: float
    status: str | None = None
    note: str | None = None


class GraphOut(BaseModel):
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    stats: dict


class DuplicateCandidate(BaseModel):
    person_id: uuid.UUID
    display_name: str
    score: int
    ratio: float
    verdict: str
    breakdown: list[dict]
    disclaimer: str


class MergeRequest(BaseModel):
    source_person_id: uuid.UUID
    confirm: bool = Field(
        description="Must be true: merging is irreversible and requires confirmation."
    )


class ImportSearchRequest(BaseModel):
    search_id: uuid.UUID


class ImportSearchResult(BaseModel):
    findings_moved: int
    profiles_created: int
    identifiers_created: int
    sources_moved: int
    runs_moved: int
