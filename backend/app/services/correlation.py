"""Correlation engine: decide whether a discovery belongs to a person.

Guiding principle: an identical username is NOT an identity. The engine looks
for additional signals (display name, bio, external links, public email,
location, platform user IDs) and always produces a cautious verdict together
with the full breakdown of the calculation.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.enums import (
    FindingStatus,
    IdentifierType,
    RelationshipType,
    VerificationStatus,
)
from app.models.evidence import Contradiction, Finding
from app.models.identity import Identifier, SocialProfile, Username
from app.models.investigation import Person
from app.services import identifiers as ident_service
from app.services.normalization import (
    normalize_domain,
    normalize_email,
    normalize_name,
    normalize_phone,
    normalize_username,
    strip_accents,
)
from app.services.scoring import Score, case_file_score, score_identity_match


@dataclass
class PersonIndex:
    """Compact view of what is known about a person, used for comparison."""

    names: set[str]
    name_tokens: set[str]
    usernames: set[str]
    emails: set[str]
    phones: set[str]
    cities: set[str]
    organizations: set[str]
    domains: set[str]
    platform_ids: set[str]


def build_index(db: Session, person: Person) -> PersonIndex:
    index = ident_service.person_identifier_values(db, person.id)

    names = {
        normalize_name(v)
        for v in (person.display_name, person.full_name)
        if v
    }
    names |= index.get(IdentifierType.NAME.value, set())
    names |= index.get(IdentifierType.ALIAS.value, set())
    if person.first_name and person.last_name:
        names.add(normalize_name(f"{person.first_name} {person.last_name}"))

    tokens: set[str] = set()
    for name in names:
        tokens |= {t for t in name.split() if len(t) > 2}
    for field in (person.first_name, person.last_name):
        if field:
            tokens.add(normalize_name(field))

    platform_ids = {
        p.platform_user_id
        for p in db.execute(
            select(SocialProfile).where(SocialProfile.person_id == person.id)
        ).scalars().all()
        if p.platform_user_id
    }

    return PersonIndex(
        names={n for n in names if n},
        name_tokens={t for t in tokens if t},
        usernames=index.get(IdentifierType.USERNAME.value, set()),
        emails=index.get(IdentifierType.EMAIL.value, set()),
        phones=index.get(IdentifierType.PHONE.value, set()),
        cities=index.get(IdentifierType.CITY.value, set())
        | index.get(IdentifierType.REGION.value, set()),
        organizations=index.get(IdentifierType.COMPANY.value, set())
        | index.get(IdentifierType.ORGANIZATION.value, set()),
        domains=index.get(IdentifierType.DOMAIN.value, set())
        | {normalize_domain(v) for v in index.get(IdentifierType.WEBSITE.value, set())},
        platform_ids=platform_ids,
    )


def score_profile_against_person(
    db: Session,
    person: Person,
    profile: SocialProfile,
    *,
    source_reliability: float = 0.5,
) -> Score:
    """Score how strongly a profile attaches to a person."""
    index = build_index(db, person)
    signals: dict[str, bool | str | None] = {}

    normalized_username = normalize_username(profile.username or "")
    if normalized_username and normalized_username in index.usernames:
        signals["username_match"] = profile.username

    display = normalize_name(profile.display_name or "")
    if display:
        if display in index.names:
            signals["name_match"] = profile.display_name
        elif index.name_tokens and _token_overlap(display, index.name_tokens) >= 2:
            signals["name_match"] = f"{profile.display_name} (correspondance partielle)"

    bio = strip_accents((profile.bio or "").lower())
    if bio:
        matched = [t for t in index.name_tokens if t and t in bio]
        matched += [c for c in index.cities if c and c in bio]
        matched += [o for o in index.organizations if o and o in bio]
        if matched:
            signals["bio_match"] = ", ".join(sorted(set(matched))[:5])

    if profile.public_email:
        if normalize_email(profile.public_email) in index.emails:
            signals["email_match"] = profile.public_email
    if profile.public_phone:
        if normalize_phone(profile.public_phone) in index.phones:
            signals["phone_match"] = profile.public_phone

    if profile.external_url:
        domain = normalize_domain(profile.external_url)
        if domain and domain in index.domains:
            signals["external_url_match"] = profile.external_url

    location = normalize_name(profile.location or "")
    if location and index.cities:
        if location in index.cities:
            signals["location_match"] = profile.location
        elif not any(location in c or c in location for c in index.cities):
            signals["location_conflict"] = f"{profile.location} vs {', '.join(sorted(index.cities)[:3])}"

    if profile.platform_user_id and profile.platform_user_id in index.platform_ids:
        signals["platform_id_match"] = profile.platform_user_id

    # A profile found only through a hypothetical variant is penalised.
    if normalized_username:
        variant = db.execute(
            select(Username).where(
                Username.person_id == person.id,
                Username.normalized_value == normalized_username,
                Username.is_variant.is_(True),
            )
        ).scalars().first()
        if variant and "username_match" not in signals:
            signals["variant_only"] = variant.value

    return score_identity_match(
        signals,
        source_reliability=source_reliability,
        human_status=profile.status,
    )


def recompute_person_score(db: Session, person: Person) -> Score:
    """Recompute and persist the case file consolidation score."""
    identifiers = db.execute(
        select(Identifier).where(Identifier.person_id == person.id)
    ).scalars().all()
    profiles = db.execute(
        select(SocialProfile).where(SocialProfile.person_id == person.id)
    ).scalars().all()
    sources = db.execute(
        select(Finding.source_id).where(Finding.person_id == person.id)
    ).scalars().all()
    open_contradictions = db.execute(
        select(Contradiction).where(
            Contradiction.person_id == person.id, Contradiction.resolved.is_(False)
        )
    ).scalars().all()

    score = case_file_score(
        confirmed_identifiers=sum(
            1 for i in identifiers if i.status == VerificationStatus.CONFIRMED.value
        ),
        total_identifiers=len(identifiers),
        confirmed_profiles=sum(
            1 for p in profiles if p.status == VerificationStatus.CONFIRMED.value
        ),
        total_profiles=len(profiles),
        distinct_sources=len({s for s in sources if s}),
        contradictions=len(open_contradictions),
    )
    person.confidence_score = score.ratio
    return score


def find_duplicate_candidates(
    db: Session, person: Person, *, threshold: int = 60
) -> list[dict]:
    """Look for other people in the same case file who may be the same person."""
    index = build_index(db, person)
    others = db.execute(
        select(Person).where(
            Person.investigation_id == person.investigation_id,
            Person.id != person.id,
            Person.is_archived.is_(False),
        )
    ).scalars().all()

    candidates: list[dict] = []
    for other in others:
        other_index = build_index(db, other)
        signals: dict[str, bool | str | None] = {}
        shared_emails = index.emails & other_index.emails
        shared_usernames = index.usernames & other_index.usernames
        shared_phones = index.phones & other_index.phones
        shared_names = index.names & other_index.names
        shared_cities = index.cities & other_index.cities

        if shared_emails:
            signals["email_match"] = ", ".join(sorted(shared_emails)[:3])
        if shared_usernames:
            signals["username_match"] = ", ".join(sorted(shared_usernames)[:3])
        if shared_phones:
            signals["phone_match"] = ", ".join(sorted(shared_phones)[:3])
        if shared_names:
            signals["name_match"] = ", ".join(sorted(shared_names)[:3])
        if shared_cities:
            signals["location_match"] = ", ".join(sorted(shared_cities)[:3])

        if not signals:
            continue
        score = score_identity_match(signals, source_reliability=0.6)
        if score.total >= threshold:
            candidates.append(
                {
                    "person_id": str(other.id),
                    "display_name": other.display_name,
                    **score.as_dict(),
                }
            )
    return sorted(candidates, key=lambda c: -c["score"])


def link_possible_duplicate(db: Session, person: Person, other_id: uuid.UUID, score: int) -> None:
    ident_service.link(
        db,
        person.investigation_id,
        "person",
        person.id,
        "person",
        other_id,
        RelationshipType.POSSIBLE_SAME_AS.value,
        confidence=score / 100.0,
        note="Possible duplicate detected automatically: merging requires approval.",
    )


def merge_persons(db: Session, target: Person, source: Person, actor: str | None = None) -> dict:
    """Merge `source` into `target`. Always triggered explicitly by a human."""
    if target.investigation_id != source.investigation_id:
        raise ValueError("cannot merge across two different case files")

    moved = {"identifiers": 0, "usernames": 0, "profiles": 0, "findings": 0}

    for identifier in db.execute(
        select(Identifier).where(Identifier.person_id == source.id)
    ).scalars().all():
        exists = db.execute(
            select(Identifier).where(
                Identifier.person_id == target.id,
                Identifier.type == identifier.type,
                Identifier.normalized_value == identifier.normalized_value,
            )
        ).scalars().first()
        if exists:
            db.delete(identifier)
        else:
            identifier.person_id = target.id
            moved["identifiers"] += 1

    for username in db.execute(
        select(Username).where(Username.person_id == source.id)
    ).scalars().all():
        exists = db.execute(
            select(Username).where(
                Username.person_id == target.id,
                Username.normalized_value == username.normalized_value,
                Username.platform_id == username.platform_id,
            )
        ).scalars().first()
        if exists:
            db.delete(username)
        else:
            username.person_id = target.id
            moved["usernames"] += 1

    for profile in db.execute(
        select(SocialProfile).where(SocialProfile.person_id == source.id)
    ).scalars().all():
        exists = db.execute(
            select(SocialProfile).where(
                SocialProfile.person_id == target.id,
                SocialProfile.platform_id == profile.platform_id,
                SocialProfile.username == profile.username,
            )
        ).scalars().first()
        if exists:
            db.delete(profile)
        else:
            profile.person_id = target.id
            moved["profiles"] += 1

    for finding in db.execute(
        select(Finding).where(Finding.person_id == source.id)
    ).scalars().all():
        finding.person_id = target.id
        moved["findings"] += 1

    ident_service.add_timeline(
        db,
        target,
        kind="merge",
        message=f"Merged '{source.display_name}' into '{target.display_name}'",
        actor=actor,
        payload=moved,
    )
    db.flush()
    db.delete(source)
    recompute_person_score(db, target)
    return moved


def open_contradictions(db: Session, person_id: uuid.UUID) -> list[Contradiction]:
    return list(
        db.execute(
            select(Contradiction).where(
                Contradiction.person_id == person_id,
                Contradiction.resolved.is_(False),
            )
        ).scalars().all()
    )


def apply_human_decision(
    db: Session, finding: Finding, decision: str, user_id: uuid.UUID | None
) -> Finding:
    """Apply an analyst decision: confirm, reject or send back for review."""
    from datetime import datetime

    mapping = {
        "confirm": FindingStatus.CONFIRMED.value,
        "reject": FindingStatus.REJECTED.value,
        "investigate": FindingStatus.UNVERIFIED.value,
        "probable": FindingStatus.PROBABLE.value,
        "outdated": FindingStatus.OUTDATED.value,
    }
    if decision not in mapping:
        raise ValueError(f"unknown decision: {decision}")

    finding.status = mapping[decision]
    finding.verified_at = datetime.now(UTC)
    finding.verified_by_id = user_id

    # A rejected item must stop weighing on scores.
    if finding.status == FindingStatus.REJECTED.value:
        finding.confidence = 0.0
    elif finding.status == FindingStatus.CONFIRMED.value:
        finding.confidence = 1.0
    return finding


def _token_overlap(value: str, tokens: set[str]) -> int:
    parts = {p for p in value.split() if len(p) > 2}
    return len(parts & tokens)
