"""Shared schemas."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    limit: int
    offset: int


class Message(BaseModel):
    detail: str


class IdResponse(BaseModel):
    id: uuid.UUID


class ScoreBreakdownItem(BaseModel):
    code: str
    label: str
    points: int
    detail: str | None = None


class ScoreOut(BaseModel):
    score: int
    ratio: float
    verdict: str
    breakdown: list[ScoreBreakdownItem]
    disclaimer: str


class TimelineEventOut(ORMModel):
    id: uuid.UUID
    at: datetime
    kind: str
    message: str
    actor: str | None = None
    payload: dict | None = None


class PaginationParams(BaseModel):
    limit: int = Field(default=50, ge=1, le=500)
    offset: int = Field(default=0, ge=0)
