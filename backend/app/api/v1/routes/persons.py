"""Person page: details, identifiers, usernames, social profiles, duplicates."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import db_session, get_current_user, get_person, require_analyst
from app.api.v1.routes.investigations import get_or_create_tag
from app.models.enums import (
    FindingStatus,
    IdentifierType,
    SourceKind,
    VerificationStatus,
)
from app.models.evidence import Contradiction, Finding, Relationship, Source
from app.models.identity import Identifier, SocialProfile, Username
from app.models.investigation import Person
from app.models.ops import Search
from app.models.user import User
from app.schemas.common import Message, ScoreOut, TimelineEventOut
from app.schemas.evidence import DuplicateCandidate, MergeRequest
from app.schemas.identity import (
    IdentifierCreate,
    IdentifierCreated,
    IdentifierOut,
    IdentifierUpdate,
    SocialProfileCreate,
    SocialProfileDetail,
    SocialProfileOut,
    StatusDecision,
    UsernameCreate,
    UsernameOut,
    UsernameUpdate,
    VariantResponse,
    VariantSaveRequest,
)
from app.schemas.investigation import (
    PersonCounters,
    PersonDetail,
    PersonOut,
    PersonUpdate,
    TagAssign,
)
from app.security import audit
from app.services import correlation, variants
from app.services import identifiers as ident_service
from app.services.orchestration import compatible_plugins

router = APIRouter(prefix="/persons", tags=["people"])


def _count(db: Session, model, **filters) -> int:
    query = select(func.count()).select_from(model)
    for column, value in filters.items():
        query = query.where(getattr(model, column) == value)
    return int(db.execute(query).scalar_one())


@router.get("/{person_id}", response_model=PersonDetail)
def get_one(
    person: Person = Depends(get_person), db: Session = Depends(db_session)
) -> dict:
    score = correlation.recompute_person_score(db, person)
    counters = PersonCounters(
        identifiers=_count(db, Identifier, person_id=person.id),
        usernames=_count(db, Username, person_id=person.id),
        social_profiles=_count(db, SocialProfile, person_id=person.id),
        findings=_count(db, Finding, person_id=person.id),
        sources=int(
            db.execute(
                select(func.count(func.distinct(Finding.source_id))).where(
                    Finding.person_id == person.id
                )
            ).scalar_one()
        ),
        relationships=int(
            db.execute(
                select(func.count())
                .select_from(Relationship)
                .where(Relationship.investigation_id == person.investigation_id)
            ).scalar_one()
        ),
        searches=_count(db, Search, person_id=person.id),
        open_contradictions=int(
            db.execute(
                select(func.count())
                .select_from(Contradiction)
                .where(
                    Contradiction.person_id == person.id,
                    Contradiction.resolved.is_(False),
                )
            ).scalar_one()
        ),
        new_findings=int(
            db.execute(
                select(func.count())
                .select_from(Finding)
                .where(
                    Finding.person_id == person.id,
                    Finding.status == FindingStatus.NEW.value,
                )
            ).scalar_one()
        ),
    )
    return {
        **PersonOut.model_validate(person).model_dump(),
        "counters": counters,
        "score": ScoreOut(**score.as_dict()),
    }


@router.patch("/{person_id}", response_model=PersonOut)
def update_person(
    payload: PersonUpdate,
    request: Request,
    person: Person = Depends(get_person),
    db: Session = Depends(db_session),
    user: User = Depends(require_analyst),
) -> Person:
    for field, value in payload.model_dump(exclude_none=True).items():
        setattr(person, field, value)
    audit.record(
        db,
        action="person.updated",
        user=user,
        object_type="person",
        object_id=person.id,
        detail=payload.model_dump(exclude_none=True),
        request=request,
    )
    return person


@router.delete("/{person_id}", response_model=Message)
def delete_person(
    request: Request,
    person: Person = Depends(get_person),
    db: Session = Depends(db_session),
    user: User = Depends(require_analyst),
    confirm: bool = Query(default=False),
) -> Message:
    if not confirm:
        raise HTTPException(status_code=400, detail="Add confirm=true to delete")
    db.delete(person)
    audit.record(
        db,
        action="person.deleted",
        user=user,
        object_type="person",
        object_id=person.id,
        request=request,
    )
    return Message(detail="Person permanently deleted")


@router.post("/{person_id}/tags", response_model=PersonOut)
def add_tag(
    payload: TagAssign,
    person: Person = Depends(get_person),
    db: Session = Depends(db_session),
    _: User = Depends(require_analyst),
) -> Person:
    tag = get_or_create_tag(db, payload.name, payload.color)
    if tag not in person.tags:
        person.tags.append(tag)
    return person


@router.delete("/{person_id}/tags/{tag_id}", response_model=PersonOut)
def remove_tag(
    tag_id: uuid.UUID,
    person: Person = Depends(get_person),
    db: Session = Depends(db_session),
    _: User = Depends(require_analyst),
) -> Person:
    person.tags = [t for t in person.tags if t.id != tag_id]
    return person


# ------------------------------------------------------------- identifiers


@router.get("/{person_id}/identifiers", response_model=list[IdentifierOut])
def list_identifiers(
    person: Person = Depends(get_person),
    db: Session = Depends(db_session),
    type: str | None = None,
) -> list[Identifier]:
    query = select(Identifier).where(Identifier.person_id == person.id)
    if type:
        query = query.where(Identifier.type == type)
    return list(db.execute(query.order_by(Identifier.created_at)).scalars().all())


@router.post("/{person_id}/identifiers", response_model=IdentifierCreated, status_code=201)
def add_identifier(
    payload: IdentifierCreate,
    request: Request,
    person: Person = Depends(get_person),
    db: Session = Depends(db_session),
    user: User = Depends(require_analyst),
) -> dict:
    """Add information to the case file and list the plugins that can use it."""
    source_id = None
    if payload.source_url:
        source = Source(
            investigation_id=person.investigation_id,
            kind=SourceKind.MANUAL_ENTRY.value,
            url=payload.source_url,
            title="Manual entry",
            reliability=0.6,
        )
        db.add(source)
        db.flush()
        source_id = source.id

    try:
        identifier, created = ident_service.add_identifier(
            db,
            person,
            identifier_type=payload.type.value,
            value=payload.value,
            platform_name=payload.platform,
            confidence=payload.confidence,
            status=payload.status.value,
            source_id=source_id,
            is_former=payload.is_former,
            note=payload.note,
            actor=user.email,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    audit.record(
        db,
        action="identifier.added",
        user=user,
        object_type="identifier",
        object_id=identifier.id,
        message=f"{payload.type.value} added to {person.display_name}",
        request=request,
    )
    return {
        "identifier": IdentifierOut.model_validate(identifier),
        "created": created,
        "compatible_plugins": compatible_plugins(db, payload.type.value),
    }


@router.patch("/{person_id}/identifiers/{identifier_id}", response_model=IdentifierOut)
def update_identifier(
    identifier_id: uuid.UUID,
    payload: IdentifierUpdate,
    person: Person = Depends(get_person),
    db: Session = Depends(db_session),
    _: User = Depends(require_analyst),
) -> Identifier:
    identifier = db.get(Identifier, identifier_id)
    if identifier is None or identifier.person_id != person.id:
        raise HTTPException(status_code=404, detail="Identifier not found")

    data = payload.model_dump(exclude_none=True)
    if "value" in data:
        from app.services.normalization import normalize

        identifier.value = data["value"]
        identifier.normalized_value = normalize(identifier.type, data["value"])
    if "platform" in data:
        from app.services import platforms as platform_service

        platform = platform_service.resolve(db, data["platform"])
        identifier.platform_id = platform.id if platform else None
    for field in ("status", "confidence", "is_former", "note"):
        if field in data:
            setattr(identifier, field, data[field].value if hasattr(data[field], "value") else data[field])
    return identifier


@router.delete("/{person_id}/identifiers/{identifier_id}", response_model=Message)
def delete_identifier(
    identifier_id: uuid.UUID,
    person: Person = Depends(get_person),
    db: Session = Depends(db_session),
    _: User = Depends(require_analyst),
) -> Message:
    identifier = db.get(Identifier, identifier_id)
    if identifier is None or identifier.person_id != person.id:
        raise HTTPException(status_code=404, detail="Identifier not found")
    db.delete(identifier)
    return Message(detail="Identifier deleted")


# --------------------------------------------------------------- usernames


@router.get("/{person_id}/usernames", response_model=list[UsernameOut])
def list_usernames(
    person: Person = Depends(get_person),
    db: Session = Depends(db_session),
    include_variants: bool = True,
) -> list[Username]:
    query = select(Username).where(Username.person_id == person.id)
    if not include_variants:
        query = query.where(Username.is_variant.is_(False))
    return list(db.execute(query.order_by(Username.created_at)).scalars().all())


@router.post("/{person_id}/usernames", response_model=UsernameOut, status_code=201)
def add_username(
    payload: UsernameCreate,
    person: Person = Depends(get_person),
    db: Session = Depends(db_session),
    user: User = Depends(require_analyst),
) -> Username:
    username, _created = ident_service.add_username(
        db,
        person,
        value=payload.value,
        platform_name=payload.platform,
        url=payload.url,
        confidence=payload.confidence,
        status=payload.status.value,
        note=payload.note,
        actor=user.email,
    )
    return username


@router.patch("/{person_id}/usernames/{username_id}", response_model=UsernameOut)
def update_username(
    username_id: uuid.UUID,
    payload: UsernameUpdate,
    person: Person = Depends(get_person),
    db: Session = Depends(db_session),
    _: User = Depends(require_analyst),
) -> Username:
    username = db.get(Username, username_id)
    if username is None or username.person_id != person.id:
        raise HTTPException(status_code=404, detail="Username not found")
    data = payload.model_dump(exclude_none=True)
    if "platform" in data:
        from app.services import platforms as platform_service

        platform = platform_service.resolve(db, data["platform"])
        username.platform_id = platform.id if platform else None
        if platform and not username.url:
            username.url = platform.profile_url(username.value)
    for field in ("url", "note", "confidence"):
        if field in data:
            setattr(username, field, data[field])
    if "status" in data:
        username.status = data["status"].value
        # Human validation clears the hypothetical flag on a variant.
        if username.status == VerificationStatus.CONFIRMED.value:
            username.is_variant = False
            username.confidence = 1.0
        elif username.status == VerificationStatus.REJECTED.value:
            username.confidence = 0.0
    return username


@router.delete("/{person_id}/usernames/{username_id}", response_model=Message)
def delete_username(
    username_id: uuid.UUID,
    person: Person = Depends(get_person),
    db: Session = Depends(db_session),
    _: User = Depends(require_analyst),
) -> Message:
    username = db.get(Username, username_id)
    if username is None or username.person_id != person.id:
        raise HTTPException(status_code=404, detail="Username not found")
    db.delete(username)
    return Message(detail="Username deleted")


@router.get("/{person_id}/username-variants", response_model=VariantResponse)
def suggest_variants(
    person: Person = Depends(get_person),
    db: Session = Depends(db_session),
    _: User = Depends(get_current_user),
    limit: int = Query(default=40, ge=1, le=200),
) -> VariantResponse:
    """Suggest variants WITHOUT storing them: these are hypotheses."""
    index = ident_service.person_identifier_values(db, person.id)
    suggestions = variants.combined(
        person.first_name,
        person.last_name,
        known_usernames=sorted(index.get(IdentifierType.USERNAME.value, set())),
        birth_year=person.date_of_birth.year if person.date_of_birth else None,
        location_codes=sorted(index.get(IdentifierType.CITY.value, set())),
        limit=limit,
    )
    return VariantResponse(suggestions=[v.as_dict() for v in suggestions])


@router.post("/{person_id}/username-variants", response_model=list[UsernameOut], status_code=201)
def save_variants(
    payload: VariantSaveRequest,
    person: Person = Depends(get_person),
    db: Session = Depends(db_session),
    user: User = Depends(require_analyst),
) -> list[Username]:
    """Store the selected variants, flagged as hypotheses."""
    saved: list[Username] = []
    for value in payload.values:
        username, _ = ident_service.add_username(
            db,
            person,
            value=value,
            confidence=0.3,
            is_variant=True,
            variant_rule="manual_selection",
            note="Hypothesis kept by the analyst, not confirmed.",
            actor=user.email,
        )
        saved.append(username)
    return saved


# --------------------------------------------------------- social profiles


@router.get("/{person_id}/social-profiles", response_model=list[SocialProfileOut])
def list_profiles(
    person: Person = Depends(get_person),
    db: Session = Depends(db_session),
    status: str | None = None,
) -> list[SocialProfile]:
    query = select(SocialProfile).where(SocialProfile.person_id == person.id)
    if status:
        query = query.where(SocialProfile.status == status)
    return list(db.execute(query.order_by(SocialProfile.created_at)).scalars().all())


@router.post("/{person_id}/social-profiles", response_model=SocialProfileOut, status_code=201)
def add_profile(
    payload: SocialProfileCreate,
    person: Person = Depends(get_person),
    db: Session = Depends(db_session),
    user: User = Depends(require_analyst),
) -> SocialProfile:
    profile, _created = ident_service.upsert_social_profile(
        db,
        person,
        platform_name=payload.platform,
        username=payload.username,
        url=payload.url,
        metadata=payload.model_dump(exclude_none=True),
        confidence=payload.confidence,
        status=payload.status.value,
        plugin="manual",
        actor=user.email,
    )
    return profile


@router.get("/{person_id}/social-profiles/{profile_id}", response_model=SocialProfileDetail)
def get_profile(
    profile_id: uuid.UUID,
    person: Person = Depends(get_person),
    db: Session = Depends(db_session),
) -> dict:
    profile = db.get(SocialProfile, profile_id)
    if profile is None or profile.person_id != person.id:
        raise HTTPException(status_code=404, detail="Profile not found")
    source = db.get(Source, profile.source_id) if profile.source_id else None
    score = correlation.score_profile_against_person(
        db, person, profile, source_reliability=source.reliability if source else 0.5
    )
    return {
        **SocialProfileOut.model_validate(profile).model_dump(),
        "score": ScoreOut(**score.as_dict()),
    }


@router.post("/{person_id}/social-profiles/{profile_id}/status", response_model=SocialProfileOut)
def decide_profile(
    profile_id: uuid.UUID,
    payload: StatusDecision,
    request: Request,
    person: Person = Depends(get_person),
    db: Session = Depends(db_session),
    user: User = Depends(require_analyst),
) -> SocialProfile:
    """Human validation of a profile: confirm, reject or send back for review."""
    profile = db.get(SocialProfile, profile_id)
    if profile is None or profile.person_id != person.id:
        raise HTTPException(status_code=404, detail="Profile not found")

    profile.status = payload.status.value
    if profile.status == VerificationStatus.CONFIRMED.value:
        profile.confidence = 1.0
    elif profile.status == VerificationStatus.REJECTED.value:
        profile.confidence = 0.0

    ident_service.add_timeline(
        db,
        person,
        kind="profile_decision",
        message=f"Profile {profile.username}: {payload.status.value}"
        + (f" ({payload.reason})" if payload.reason else ""),
        actor=user.email,
        payload={"profile_id": str(profile.id)},
    )
    audit.record(
        db,
        action="profile.decision",
        user=user,
        object_type="social_profile",
        object_id=profile.id,
        detail={"status": payload.status.value, "reason": payload.reason},
        request=request,
    )
    correlation.recompute_person_score(db, person)
    return profile


@router.delete("/{person_id}/social-profiles/{profile_id}", response_model=Message)
def delete_profile(
    profile_id: uuid.UUID,
    person: Person = Depends(get_person),
    db: Session = Depends(db_session),
    _: User = Depends(require_analyst),
) -> Message:
    profile = db.get(SocialProfile, profile_id)
    if profile is None or profile.person_id != person.id:
        raise HTTPException(status_code=404, detail="Profile not found")
    db.delete(profile)
    return Message(detail="Profile deleted")


# ------------------------------------------------------------- duplicates


@router.get("/{person_id}/duplicates", response_model=list[DuplicateCandidate])
def duplicates(
    person: Person = Depends(get_person),
    db: Session = Depends(db_session),
    _: User = Depends(get_current_user),
    threshold: int = Query(default=60, ge=0, le=100),
) -> list[dict]:
    return correlation.find_duplicate_candidates(db, person, threshold=threshold)


@router.post("/{person_id}/merge", response_model=Message)
def merge(
    payload: MergeRequest,
    request: Request,
    person: Person = Depends(get_person),
    db: Session = Depends(db_session),
    user: User = Depends(require_analyst),
) -> Message:
    """Merge another person into this one. Irreversible, confirmation required."""
    if not payload.confirm:
        raise HTTPException(
            status_code=400, detail="The merge must be confirmed (confirm=true)"
        )
    source = db.get(Person, payload.source_person_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Source person not found")
    if source.id == person.id:
        raise HTTPException(status_code=400, detail="Cannot merge a person into itself")

    try:
        moved = correlation.merge_persons(db, person, source, actor=user.email)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    audit.record(
        db,
        action="person.merged",
        user=user,
        object_type="person",
        object_id=person.id,
        detail={"source_person_id": str(payload.source_person_id), **moved},
        request=request,
    )
    return Message(detail=f"Merge completed: {moved}")


@router.get("/{person_id}/timeline", response_model=list[TimelineEventOut])
def timeline(
    person: Person = Depends(get_person),
    db: Session = Depends(db_session),
    limit: int = Query(default=200, ge=1, le=1000),
) -> list:
    from app.models.evidence import TimelineEvent

    return list(
        db.execute(
            select(TimelineEvent)
            .where(TimelineEvent.person_id == person.id)
            .order_by(TimelineEvent.at.desc())
            .limit(limit)
        ).scalars().all()
    )
