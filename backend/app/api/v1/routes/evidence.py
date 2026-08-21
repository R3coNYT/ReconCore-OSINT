"""Findings, sources, relationships and contradictions."""
from __future__ import annotations

import uuid
from datetime import UTC

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import db_session, get_current_user, require_analyst
from app.models.evidence import Contradiction, Finding, Relationship, Source
from app.models.investigation import Person
from app.models.user import User
from app.schemas.common import Message
from app.schemas.evidence import (
    ContradictionOut,
    ContradictionResolve,
    FindingDecision,
    FindingOut,
    RelationshipOut,
    SourceCreate,
    SourceOut,
)
from app.security import audit
from app.services import correlation, decisions
from app.services import identifiers as ident_service

router = APIRouter(tags=["evidence"])


@router.get("/findings", response_model=list[FindingOut])
def list_findings(
    db: Session = Depends(db_session),
    _: User = Depends(get_current_user),
    person_id: uuid.UUID | None = None,
    investigation_id: uuid.UUID | None = None,
    status: str | None = None,
    type: str | None = None,
    plugin: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[Finding]:
    query = select(Finding).order_by(Finding.discovered_at.desc().nullslast())
    if person_id:
        query = query.where(Finding.person_id == person_id)
    if investigation_id:
        query = query.where(Finding.investigation_id == investigation_id)
    if status:
        query = query.where(Finding.status == status)
    if type:
        query = query.where(Finding.type == type)
    if plugin:
        query = query.where(Finding.plugin == plugin)
    return list(db.execute(query.limit(limit).offset(offset)).scalars().all())


@router.get("/findings/{finding_id}", response_model=FindingOut)
def get_finding(finding_id: uuid.UUID, db: Session = Depends(db_session),
                _: User = Depends(get_current_user)) -> Finding:
    finding = db.get(Finding, finding_id)
    if finding is None:
        raise HTTPException(status_code=404, detail="Finding not found")
    return finding


@router.post("/findings/{finding_id}/decision", response_model=FindingOut)
def decide(
    finding_id: uuid.UUID,
    payload: FindingDecision,
    request: Request,
    db: Session = Depends(db_session),
    user: User = Depends(require_analyst),
) -> Finding:
    """Human validation: confirm, reject or flag for review."""
    finding = db.get(Finding, finding_id)
    if finding is None:
        raise HTTPException(status_code=404, detail="Finding not found")

    correlation.apply_human_decision(db, finding, payload.decision, user.id)
    # The profile, username and identifier describing the same account each
    # carry their own status: without this, three other tabs would keep
    # contradicting the decision the analyst just made.
    counts = decisions.propagate_from_finding(db, finding, user_id=user.id)

    person = db.get(Person, finding.person_id) if finding.person_id else None
    if person is not None:
        ident_service.add_timeline(
            db,
            person,
            kind="finding_decision",
            message=f"{finding.title} -> {finding.status}"
            + (f" ({payload.reason})" if payload.reason else "")
            + decisions.summarize(counts),
            actor=user.email,
            payload={"finding_id": str(finding.id)},
        )
        correlation.recompute_person_score(db, person)

    audit.record(
        db,
        action="finding.decision",
        user=user,
        object_type="finding",
        object_id=finding.id,
        detail={
            "decision": payload.decision,
            "reason": payload.reason,
            "propagated": counts,
        },
        request=request,
    )
    return finding


@router.get("/sources", response_model=list[SourceOut])
def list_sources(
    db: Session = Depends(db_session),
    _: User = Depends(get_current_user),
    investigation_id: uuid.UUID | None = None,
    plugin: str | None = None,
    kind: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[Source]:
    query = select(Source).order_by(Source.date_discovered.desc().nullslast())
    if investigation_id:
        query = query.where(Source.investigation_id == investigation_id)
    if plugin:
        query = query.where(Source.plugin == plugin)
    if kind:
        query = query.where(Source.kind == kind)
    return list(db.execute(query.limit(limit).offset(offset)).scalars().all())


@router.post("/investigations/{investigation_id}/sources", response_model=SourceOut, status_code=201)
def create_source(
    investigation_id: uuid.UUID,
    payload: SourceCreate,
    db: Session = Depends(db_session),
    _: User = Depends(require_analyst),
) -> Source:
    from datetime import datetime

    source = Source(
        investigation_id=investigation_id,
        kind=payload.kind.value,
        url=payload.url,
        title=payload.title,
        description=payload.description,
        reliability=payload.reliability,
        date_discovered=datetime.now(UTC),
        date_checked=datetime.now(UTC),
    )
    db.add(source)
    db.flush()
    return source


@router.get("/relationships", response_model=list[RelationshipOut])
def list_relationships(
    investigation_id: uuid.UUID,
    db: Session = Depends(db_session),
    _: User = Depends(get_current_user),
    type: str | None = None,
) -> list[Relationship]:
    query = select(Relationship).where(Relationship.investigation_id == investigation_id)
    if type:
        query = query.where(Relationship.type == type)
    return list(db.execute(query).scalars().all())


@router.get("/contradictions", response_model=list[ContradictionOut])
def list_contradictions(
    db: Session = Depends(db_session),
    _: User = Depends(get_current_user),
    person_id: uuid.UUID | None = None,
    investigation_id: uuid.UUID | None = None,
    resolved: bool | None = None,
) -> list[Contradiction]:
    query = select(Contradiction).order_by(Contradiction.created_at.desc())
    if person_id:
        query = query.where(Contradiction.person_id == person_id)
    if investigation_id:
        query = query.where(Contradiction.investigation_id == investigation_id)
    if resolved is not None:
        query = query.where(Contradiction.resolved.is_(resolved))
    return list(db.execute(query).scalars().all())


@router.post("/contradictions/{contradiction_id}/resolve", response_model=ContradictionOut)
def resolve_contradiction(
    contradiction_id: uuid.UUID,
    payload: ContradictionResolve,
    request: Request,
    db: Session = Depends(db_session),
    user: User = Depends(require_analyst),
) -> Contradiction:
    """Resolution is always a human decision, never automatic."""
    contradiction = db.get(Contradiction, contradiction_id)
    if contradiction is None:
        raise HTTPException(status_code=404, detail="Contradiction not found")

    contradiction.resolved = True
    contradiction.resolved_value = payload.resolved_value
    contradiction.resolution = payload.resolution
    contradiction.resolved_by_id = user.id

    person = db.get(Person, contradiction.person_id) if contradiction.person_id else None
    if person is not None:
        ident_service.add_timeline(
            db,
            person,
            kind="contradiction_resolved",
            message=f"Contradiction on {contradiction.field} resolved: {payload.resolved_value}",
            actor=user.email,
        )
        correlation.recompute_person_score(db, person)

    audit.record(
        db,
        action="contradiction.resolved",
        user=user,
        object_type="contradiction",
        object_id=contradiction.id,
        detail={"resolved_value": payload.resolved_value},
        request=request,
    )
    return contradiction


@router.delete("/sources/{source_id}", response_model=Message)
def delete_source(
    source_id: uuid.UUID,
    db: Session = Depends(db_session),
    _: User = Depends(require_analyst),
) -> Message:
    source = db.get(Source, source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found")
    db.delete(source)
    return Message(detail="Source deleted")
