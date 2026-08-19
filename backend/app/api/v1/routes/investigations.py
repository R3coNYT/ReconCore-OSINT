"""Investigation case files: CRUD, statistics, people, notes."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from slugify import slugify
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import (
    db_session,
    get_current_user,
    get_investigation,
    require_analyst,
)
from app.models.evidence import Contradiction, Finding, Relationship, Source
from app.models.identity import Identifier, SocialProfile, Username
from app.models.investigation import Investigation, Note, Organization, Person, Tag
from app.models.ops import Search
from app.models.user import User
from app.schemas.common import Message
from app.schemas.investigation import (
    InvestigationCreate,
    InvestigationDetail,
    InvestigationOut,
    InvestigationStats,
    InvestigationUpdate,
    NoteCreate,
    NoteOut,
    OrganizationCreate,
    OrganizationOut,
    PersonCreate,
    PersonOut,
)
from app.security import audit

router = APIRouter(prefix="/investigations", tags=["investigations"])


def _count(db: Session, model, **filters) -> int:
    query = select(func.count()).select_from(model)
    for column, value in filters.items():
        query = query.where(getattr(model, column) == value)
    return int(db.execute(query).scalar_one())


@router.get("", response_model=list[InvestigationOut])
def list_investigations(
    db: Session = Depends(db_session),
    _: User = Depends(get_current_user),
    entity_type: str | None = None,
    status: str | None = None,
    search: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[Investigation]:
    query = select(Investigation).order_by(Investigation.updated_at.desc())
    if entity_type:
        query = query.where(Investigation.entity_type == entity_type)
    if status:
        query = query.where(Investigation.status == status)
    if search:
        query = query.where(Investigation.title.ilike(f"%{search}%"))
    return list(db.execute(query.limit(limit).offset(offset)).scalars().all())


@router.post("", response_model=InvestigationOut, status_code=201)
def create_investigation(
    payload: InvestigationCreate,
    request: Request,
    db: Session = Depends(db_session),
    user: User = Depends(require_analyst),
) -> Investigation:
    investigation = Investigation(
        title=payload.title,
        entity_type=payload.entity_type.value,
        description=payload.description,
        legal_basis=payload.legal_basis,
        automation_enabled=payload.automation_enabled,
        default_depth=payload.default_depth,
        retention_until=payload.retention_until,
        owner_id=user.id,
    )
    db.add(investigation)
    db.flush()
    audit.record(
        db,
        action="investigation.created",
        user=user,
        object_type="investigation",
        object_id=investigation.id,
        message=f"Case file created: {investigation.title}",
        request=request,
    )
    return investigation


@router.get("/{investigation_id}", response_model=InvestigationDetail)
def get_one(
    investigation: Investigation = Depends(get_investigation),
    db: Session = Depends(db_session),
) -> dict:
    person_ids = [
        p.id
        for p in db.execute(
            select(Person).where(Person.investigation_id == investigation.id)
        ).scalars().all()
    ]
    last_search = db.execute(
        select(func.max(Search.started_at)).where(
            Search.investigation_id == investigation.id
        )
    ).scalar()

    stats = InvestigationStats(
        persons=len(person_ids),
        identifiers=_count(db, Identifier, investigation_id=investigation.id),
        usernames=_count(db, Username, investigation_id=investigation.id),
        social_profiles=_count(db, SocialProfile, investigation_id=investigation.id),
        findings=_count(db, Finding, investigation_id=investigation.id),
        sources=_count(db, Source, investigation_id=investigation.id),
        relationships=_count(db, Relationship, investigation_id=investigation.id),
        searches=_count(db, Search, investigation_id=investigation.id),
        open_contradictions=int(
            db.execute(
                select(func.count())
                .select_from(Contradiction)
                .where(
                    Contradiction.investigation_id == investigation.id,
                    Contradiction.resolved.is_(False),
                )
            ).scalar_one()
        ),
        last_search_at=last_search,
    )
    return {
        **InvestigationOut.model_validate(investigation).model_dump(),
        "stats": stats,
    }


@router.patch("/{investigation_id}", response_model=InvestigationOut)
def update_investigation(
    payload: InvestigationUpdate,
    request: Request,
    investigation: Investigation = Depends(get_investigation),
    db: Session = Depends(db_session),
    user: User = Depends(require_analyst),
) -> Investigation:
    for field, value in payload.model_dump(exclude_none=True).items():
        setattr(investigation, field, value)
    audit.record(
        db,
        action="investigation.updated",
        user=user,
        object_type="investigation",
        object_id=investigation.id,
        detail=payload.model_dump(exclude_none=True),
        request=request,
    )
    return investigation


@router.delete("/{investigation_id}", response_model=Message)
def delete_investigation(
    request: Request,
    investigation: Investigation = Depends(get_investigation),
    db: Session = Depends(db_session),
    user: User = Depends(require_analyst),
    confirm: bool = Query(
        default=False, description="Permanent deletion: must be true."
    ),
) -> Message:
    if not confirm:
        raise HTTPException(
            status_code=400,
            detail="Permanent deletion: call again with confirm=true",
        )
    title = investigation.title
    db.delete(investigation)
    audit.record(
        db,
        action="investigation.deleted",
        user=user,
        object_type="investigation",
        object_id=investigation.id,
        message=f"Case file permanently deleted: {title}",
        request=request,
    )
    return Message(detail="Case file permanently deleted")


# --------------------------------------------------------------------- people


@router.get("/{investigation_id}/persons", response_model=list[PersonOut])
def list_persons(
    investigation: Investigation = Depends(get_investigation),
    db: Session = Depends(db_session),
    include_archived: bool = False,
) -> list[Person]:
    query = select(Person).where(Person.investigation_id == investigation.id)
    if not include_archived:
        query = query.where(Person.is_archived.is_(False))
    return list(db.execute(query.order_by(Person.created_at)).scalars().all())


@router.post("/{investigation_id}/persons", response_model=PersonOut, status_code=201)
def create_person(
    payload: PersonCreate,
    request: Request,
    investigation: Investigation = Depends(get_investigation),
    db: Session = Depends(db_session),
    user: User = Depends(require_analyst),
) -> Person:
    person = Person(
        investigation_id=investigation.id,
        **payload.model_dump(exclude_none=True),
    )
    db.add(person)
    db.flush()
    from app.services.identifiers import add_timeline

    add_timeline(
        db,
        person,
        kind="person_created",
        message=f"Person case file created: {person.display_name}",
        actor=user.email,
    )
    audit.record(
        db,
        action="person.created",
        user=user,
        object_type="person",
        object_id=person.id,
        request=request,
    )
    return person


@router.post(
    "/{investigation_id}/organizations", response_model=OrganizationOut, status_code=201
)
def create_organization(
    payload: OrganizationCreate,
    investigation: Investigation = Depends(get_investigation),
    db: Session = Depends(db_session),
    _: User = Depends(require_analyst),
) -> Organization:
    organization = Organization(
        investigation_id=investigation.id, **payload.model_dump(exclude_none=True)
    )
    db.add(organization)
    db.flush()
    return organization


@router.get("/{investigation_id}/organizations", response_model=list[OrganizationOut])
def list_organizations(
    investigation: Investigation = Depends(get_investigation),
    db: Session = Depends(db_session),
) -> list[Organization]:
    return list(
        db.execute(
            select(Organization).where(Organization.investigation_id == investigation.id)
        ).scalars().all()
    )


# ---------------------------------------------------------------------- notes


@router.get("/{investigation_id}/notes", response_model=list[NoteOut])
def list_notes(
    investigation: Investigation = Depends(get_investigation),
    db: Session = Depends(db_session),
    person_id: uuid.UUID | None = None,
) -> list[Note]:
    query = select(Note).where(Note.investigation_id == investigation.id)
    if person_id:
        query = query.where(Note.person_id == person_id)
    return list(db.execute(query.order_by(Note.created_at.desc())).scalars().all())


@router.post("/{investigation_id}/notes", response_model=NoteOut, status_code=201)
def create_note(
    payload: NoteCreate,
    investigation: Investigation = Depends(get_investigation),
    db: Session = Depends(db_session),
    user: User = Depends(require_analyst),
) -> Note:
    note = Note(
        investigation_id=investigation.id,
        person_id=payload.person_id,
        title=payload.title,
        body=payload.body,
        author_id=user.id,
    )
    db.add(note)
    db.flush()
    return note


def get_or_create_tag(db: Session, name: str, color: str | None = None) -> Tag:
    slug = slugify(name)
    tag = db.execute(select(Tag).where(Tag.slug == slug)).scalar_one_or_none()
    if tag is None:
        tag = Tag(name=name, slug=slug, color=color)
        db.add(tag)
        db.flush()
    return tag
