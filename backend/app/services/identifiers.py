"""Adding and managing a person's identifiers.

Every write goes through this module, which guarantees normalisation,
deduplication, contradiction detection and timeline logging.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.enums import (
    IdentifierType,
    RelationshipType,
    VerificationStatus,
)
from app.models.evidence import Contradiction, Relationship, TimelineEvent
from app.models.identity import Identifier, Platform, SocialProfile, Username
from app.models.investigation import Person
from app.services import platforms as platform_service
from app.services.normalization import normalize

#: Fields where two different values are a contradiction worth flagging
#: (a person normally has a single current city of residence, etc.).
SINGLE_VALUE_TYPES = {
    IdentifierType.CITY.value,
    IdentifierType.DEPARTMENT.value,
    IdentifierType.REGION.value,
    IdentifierType.COUNTRY.value,
    IdentifierType.DATE_OF_BIRTH.value,
    IdentifierType.ADDRESS.value,
}

IDENTIFIER_RELATION = {
    IdentifierType.EMAIL.value: RelationshipType.HAS_EMAIL.value,
    IdentifierType.PHONE.value: RelationshipType.HAS_PHONE.value,
    IdentifierType.USERNAME.value: RelationshipType.USES_USERNAME.value,
    IdentifierType.CITY.value: RelationshipType.LOCATED_IN.value,
    IdentifierType.COUNTRY.value: RelationshipType.LOCATED_IN.value,
    IdentifierType.COMPANY.value: RelationshipType.WORKS_FOR.value,
    IdentifierType.ORGANIZATION.value: RelationshipType.WORKS_FOR.value,
    IdentifierType.DOMAIN.value: RelationshipType.ASSOCIATED_WITH.value,
    IdentifierType.WEBSITE.value: RelationshipType.ASSOCIATED_WITH.value,
}


def _now() -> datetime:
    return datetime.now(UTC)


def add_identifier(
    db: Session,
    person: Person,
    *,
    identifier_type: str,
    value: str,
    platform_name: str | None = None,
    confidence: float = 0.5,
    status: str = VerificationStatus.UNKNOWN.value,
    source_id: uuid.UUID | None = None,
    is_former: bool = False,
    note: str | None = None,
    actor: str | None = None,
) -> tuple[Identifier, bool]:
    """Create the identifier if missing. Returns (identifier, created)."""
    value = value.strip()
    if not value:
        raise ValueError("empty value")

    normalized = normalize(identifier_type, value)
    platform = (
        platform_service.resolve(db, platform_name) if platform_name else None
    )

    existing = db.execute(
        select(Identifier).where(
            Identifier.person_id == person.id,
            Identifier.type == identifier_type,
            Identifier.normalized_value == normalized,
        )
    ).scalars().first()

    if existing:
        # Never downgrade information a human has already validated.
        if existing.status != VerificationStatus.CONFIRMED.value:
            existing.confidence = max(existing.confidence, confidence)
        if platform and not existing.platform_id:
            existing.platform_id = platform.id
        if source_id and not existing.source_id:
            existing.source_id = source_id
        return existing, False

    _detect_contradiction(db, person, identifier_type, value, normalized)

    identifier = Identifier(
        investigation_id=person.investigation_id,
        person_id=person.id,
        type=identifier_type,
        value=value[:500],
        normalized_value=normalized[:500],
        platform_id=platform.id if platform else None,
        confidence=confidence,
        status=status,
        source_id=source_id,
        is_former=is_former,
        note=note,
    )
    db.add(identifier)
    db.flush()

    relation = IDENTIFIER_RELATION.get(identifier_type)
    if relation:
        link(
            db,
            person.investigation_id,
            "person",
            person.id,
            "identifier",
            identifier.id,
            relation,
            confidence=confidence,
        )

    add_timeline(
        db,
        person,
        kind="identifier_added",
        message=f"{identifier_type} added: {value}",
        actor=actor,
        payload={"identifier_id": str(identifier.id), "status": status},
    )

    # A username entered as an identifier also feeds the dedicated table.
    if identifier_type in {IdentifierType.USERNAME.value, IdentifierType.ALIAS.value}:
        add_username(
            db,
            person,
            value=value,
            platform_name=platform_name,
            confidence=confidence,
            status=status,
            source_id=source_id,
            actor=actor,
        )

    return identifier, True


def add_username(
    db: Session,
    person: Person,
    *,
    value: str,
    platform_name: str | None = None,
    url: str | None = None,
    confidence: float = 0.4,
    status: str = VerificationStatus.UNKNOWN.value,
    source_id: uuid.UUID | None = None,
    is_variant: bool = False,
    variant_of_id: uuid.UUID | None = None,
    variant_rule: str | None = None,
    note: str | None = None,
    actor: str | None = None,
) -> tuple[Username, bool]:
    """Store a username. A username with no platform is valid and searchable."""
    value = value.strip().lstrip("@")
    if not value:
        raise ValueError("empty username")

    normalized = normalize(IdentifierType.USERNAME.value, value)
    platform = platform_service.resolve(db, platform_name) if platform_name else None
    platform_id = platform.id if platform else None

    existing = db.execute(
        select(Username).where(
            Username.person_id == person.id,
            Username.normalized_value == normalized,
            Username.platform_id.is_(platform_id) if platform_id is None
            else Username.platform_id == platform_id,
        )
    ).scalars().first()

    if existing:
        if existing.status != VerificationStatus.CONFIRMED.value:
            existing.confidence = max(existing.confidence, confidence)
        if url and not existing.url:
            existing.url = url
        return existing, False

    if url is None and platform is not None:
        url = platform.profile_url(value)

    username = Username(
        investigation_id=person.investigation_id,
        person_id=person.id,
        value=value[:200],
        normalized_value=normalized[:200],
        platform_id=platform_id,
        url=url,
        status=(
            VerificationStatus.HYPOTHESIS.value if is_variant else status
        ),
        confidence=min(confidence, 0.35) if is_variant else confidence,
        is_variant=is_variant,
        variant_of_id=variant_of_id,
        variant_rule=variant_rule,
        source_id=source_id,
        discovered_at=_now(),
        note=note,
    )
    db.add(username)
    db.flush()

    link(
        db,
        person.investigation_id,
        "person",
        person.id,
        "username",
        username.id,
        RelationshipType.VARIANT_OF.value if is_variant else RelationshipType.USES_USERNAME.value,
        confidence=username.confidence,
    )
    if platform_id:
        link(
            db,
            person.investigation_id,
            "username",
            username.id,
            "platform",
            platform_id,
            RelationshipType.EXISTS_ON.value,
            confidence=username.confidence,
        )

    add_timeline(
        db,
        person,
        kind="username_added",
        message=(
            f"{'Hypothetical username' if is_variant else 'Username'} added: {value}"
            + (f" ({platform.name})" if platform else " (platform unknown)")
        ),
        actor=actor,
        payload={"username_id": str(username.id), "is_variant": is_variant},
    )
    return username, True


def upsert_social_profile(
    db: Session,
    person: Person,
    *,
    platform_name: str | None,
    username: str,
    url: str | None = None,
    metadata: dict | None = None,
    confidence: float = 0.35,
    status: str = VerificationStatus.HYPOTHESIS.value,
    source_id: uuid.UUID | None = None,
    plugin: str | None = None,
    actor: str | None = None,
) -> tuple[SocialProfile, bool]:
    """Create or enrich a discovered social profile."""
    platform = platform_service.resolve(db, platform_name) if platform_name else None
    platform_id = platform.id if platform else None
    metadata = metadata or {}

    existing = db.execute(
        select(SocialProfile).where(
            SocialProfile.person_id == person.id,
            SocialProfile.platform_id == platform_id,
            SocialProfile.username == username,
        )
    ).scalars().first()

    profile = existing or SocialProfile(
        investigation_id=person.investigation_id,
        person_id=person.id,
        platform_id=platform_id,
        username=username[:200],
        status=status,
        confidence=confidence,
    )
    created = existing is None
    if created:
        db.add(profile)

    profile.url = url or profile.url or (platform.profile_url(username) if platform else None)
    if profile.status != VerificationStatus.CONFIRMED.value:
        profile.confidence = max(profile.confidence or 0.0, confidence)
    profile.last_checked_at = _now()
    profile.discovered_by_plugin = plugin or profile.discovered_by_plugin
    if source_id:
        profile.source_id = source_id

    for field in (
        "display_name", "bio", "avatar_url", "external_url", "location",
        "public_email", "public_phone", "followers", "following", "posts_count",
        "is_verified", "is_private", "is_business", "platform_user_id",
    ):
        value = metadata.get(field)
        if value is not None and getattr(profile, field) in (None, ""):
            setattr(profile, field, value)

    merged_raw = dict(profile.raw or {})
    merged_raw.update({k: v for k, v in metadata.items() if v is not None})
    profile.raw = merged_raw
    db.flush()

    if created:
        link(
            db,
            person.investigation_id,
            "person",
            person.id,
            "social_profile",
            profile.id,
            RelationshipType.HAS_PROFILE.value,
            confidence=profile.confidence,
        )
        if platform_id:
            link(
                db,
                person.investigation_id,
                "social_profile",
                profile.id,
                "platform",
                platform_id,
                RelationshipType.EXISTS_ON.value,
                confidence=profile.confidence,
            )
        add_timeline(
            db,
            person,
            kind="profile_discovered",
            message=f"Profile discovered: {platform.name if platform else 'unknown platform'} / {username}",
            actor=actor or plugin,
            payload={"profile_id": str(profile.id), "url": profile.url},
        )
    return profile, created


def link(
    db: Session,
    investigation_id: uuid.UUID,
    source_type: str,
    source_ref: uuid.UUID | str,
    target_type: str,
    target_ref: uuid.UUID | str,
    relation_type: str,
    *,
    confidence: float = 0.5,
    source_id: uuid.UUID | None = None,
    note: str | None = None,
) -> Relationship:
    """Create the edge unless it already exists."""
    existing = db.execute(
        select(Relationship).where(
            Relationship.investigation_id == investigation_id,
            Relationship.source_type == source_type,
            Relationship.source_ref == str(source_ref),
            Relationship.target_type == target_type,
            Relationship.target_ref == str(target_ref),
            Relationship.type == relation_type,
        )
    ).scalars().first()
    if existing:
        existing.confidence = max(existing.confidence, confidence)
        return existing

    relationship = Relationship(
        investigation_id=investigation_id,
        source_type=source_type,
        source_ref=str(source_ref),
        target_type=target_type,
        target_ref=str(target_ref),
        type=relation_type,
        confidence=confidence,
        evidence_source_id=source_id,
        note=note,
    )
    db.add(relationship)
    db.flush()
    return relationship


def add_timeline(
    db: Session,
    person: Person | None,
    *,
    kind: str,
    message: str,
    investigation_id: uuid.UUID | None = None,
    actor: str | None = None,
    payload: dict | None = None,
) -> TimelineEvent:
    event = TimelineEvent(
        investigation_id=investigation_id or (person.investigation_id if person else None),
        person_id=person.id if person else None,
        at=_now(),
        kind=kind,
        message=message[:600],
        actor=actor,
        payload=payload,
    )
    db.add(event)
    return event


def _detect_contradiction(
    db: Session, person: Person, identifier_type: str, value: str, normalized: str
) -> None:
    """Flag (without deciding) two incompatible values for a single-valued field."""
    if identifier_type not in SINGLE_VALUE_TYPES:
        return
    others = db.execute(
        select(Identifier).where(
            Identifier.person_id == person.id,
            Identifier.type == identifier_type,
            Identifier.is_former.is_(False),
            Identifier.status != VerificationStatus.REJECTED.value,
        )
    ).scalars().all()
    for other in others:
        if other.normalized_value == normalized:
            continue
        already = db.execute(
            select(Contradiction).where(
                Contradiction.person_id == person.id,
                Contradiction.field == identifier_type,
                Contradiction.value_a == other.value,
                Contradiction.value_b == value,
            )
        ).scalars().first()
        if already:
            continue
        db.add(
            Contradiction(
                investigation_id=person.investigation_id,
                person_id=person.id,
                field=identifier_type,
                value_a=other.value,
                value_b=value[:500],
            )
        )
        add_timeline(
            db,
            person,
            kind="contradiction",
            message=f"Contradictory information ({identifier_type}): {other.value} / {value}",
            payload={"field": identifier_type},
        )


def person_identifier_values(db: Session, person_id: uuid.UUID) -> dict[str, set[str]]:
    """{type: {normalised values}} index used by the correlation engine."""
    rows = db.execute(
        select(Identifier.type, Identifier.normalized_value).where(
            Identifier.person_id == person_id,
            Identifier.status != VerificationStatus.REJECTED.value,
        )
    ).all()
    index: dict[str, set[str]] = {}
    for identifier_type, value in rows:
        index.setdefault(identifier_type, set()).add(value)
    usernames = db.execute(
        select(Username.normalized_value).where(
            Username.person_id == person_id,
            Username.status != VerificationStatus.REJECTED.value,
        )
    ).scalars().all()
    if usernames:
        index.setdefault(IdentifierType.USERNAME.value, set()).update(usernames)
    return index


def get_platform_name(db: Session, platform_id: uuid.UUID | None) -> str | None:
    if not platform_id:
        return None
    platform = db.get(Platform, platform_id)
    return platform.name if platform else None
