"""Persisting plugin results.

Plugins never touch the database: they produce normalised items. This module
turns them into Source + Finding + entities (profiles, identifiers), while
deduplicating and always preserving provenance.
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models.enums import (
    FindingStatus,
    FindingType,
    IdentifierType,
    RelationshipType,
    VerificationStatus,
)
from app.models.evidence import Finding, Source
from app.models.investigation import Person
from app.models.ops import PluginRun, SearchResult
from app.services import identifiers as ident_service
from app.services.correlation import score_profile_against_person
from app.services.normalization import normalize

logger = get_logger(__name__)

#: Item kinds that create or enrich a social profile.
PROFILE_KINDS = {FindingType.SOCIAL_PROFILE.value, FindingType.PROFILE_METADATA.value}

#: Statuses that come from a human decision: never recomputed.
DECIDED_STATUSES = {
    VerificationStatus.CONFIRMED.value,
    VerificationStatus.REJECTED.value,
}
DECIDED_FINDING_STATUSES = {
    FindingStatus.CONFIRMED.value,
    FindingStatus.REJECTED.value,
}


def _now() -> datetime:
    return datetime.now(UTC)


def ingest_items(
    db: Session,
    run: PluginRun,
    items: list[dict],
    *,
    person: Person | None = None,
) -> dict:
    """Persist a run's items. Returns statistics plus new targets.

    `new_targets` feeds depth-based search: these are the discovered identifiers
    that may be submitted to other plugins at the next level.
    """
    stats = {
        "findings_created": 0,
        "findings_duplicated": 0,
        "profiles_created": 0,
        "identifiers_created": 0,
        "new_targets": [],
        "warnings": [],
    }

    for raw_item in items:
        item = _coerce(raw_item)
        source = _get_or_create_source(db, run, item)
        finding, created = _get_or_create_finding(db, run, item, source, person)
        stats["findings_created" if created else "findings_duplicated"] += 1

        db.add(
            SearchResult(
                run_id=run.id,
                kind=item["kind"],
                payload=item["payload"],
                dedup_key=item.get("dedup_key"),
                finding_id=finding.id,
                is_duplicate=not created,
            )
        )

        if person is None:
            continue

        if item["kind"] in PROFILE_KINDS:
            _ingest_profile(db, run, person, item, source, finding, stats)

        for derived in item.get("derived_identifiers", []):
            _ingest_identifier(db, person, derived, source, stats, run.plugin)

        for warning in item.get("warnings", []):
            if warning not in stats["warnings"]:
                stats["warnings"].append(warning)

    run.items_found = (run.items_found or 0) + stats["findings_created"]
    db.flush()
    return stats


# --------------------------------------------------------------------- detail


def _ingest_profile(
    db: Session,
    run: PluginRun,
    person: Person,
    item: dict,
    source: Source,
    finding: Finding,
    stats: dict,
) -> None:
    payload = item["payload"]
    platform_name = payload.get("platform")
    username = payload.get("username")
    if not username:
        return

    profile, created = ident_service.upsert_social_profile(
        db,
        person,
        platform_name=platform_name,
        username=str(username),
        url=payload.get("url"),
        metadata=payload,
        confidence=item.get("confidence", 0.35),
        status=VerificationStatus.HYPOTHESIS.value,
        source_id=source.id,
        plugin=run.plugin,
    )
    if created:
        stats["profiles_created"] += 1

    # The observed username becomes a known username, linked to its platform.
    ident_service.add_username(
        db,
        person,
        value=str(username),
        platform_name=platform_name,
        url=payload.get("url"),
        confidence=item.get("confidence", 0.35),
        status=VerificationStatus.HYPOTHESIS.value,
        source_id=source.id,
        actor=run.plugin,
    )

    # Explainable score for attaching this profile to the person.
    score = score_profile_against_person(db, person, profile, source_reliability=source.reliability)
    # A human decision beats any recomputation: a confirmed or rejected item
    # is never re-scored by a later plugin run.
    if profile.status not in DECIDED_STATUSES:
        profile.confidence = score.ratio
    if finding.status not in DECIDED_FINDING_STATUSES:
        finding.confidence = score.ratio
    finding.score_explanation = score.as_dict()
    ident_service.link(
        db,
        person.investigation_id,
        "finding",
        finding.id,
        "source",
        source.id,
        RelationshipType.SUPPORTED_BY.value,
        confidence=source.reliability,
    )

    # Public metadata that can become new identifiers.
    for field, identifier_type in (
        ("public_email", IdentifierType.EMAIL.value),
        ("public_phone", IdentifierType.PHONE.value),
        ("external_url", IdentifierType.WEBSITE.value),
        ("location", IdentifierType.CITY.value),
    ):
        value = payload.get(field)
        if value:
            _ingest_identifier(
                db,
                person,
                {
                    "type": identifier_type,
                    "value": str(value),
                    "confidence": 0.6,
                    "status": VerificationStatus.PROBABLE.value,
                },
                source,
                stats,
                run.plugin,
            )


def _ingest_identifier(
    db: Session,
    person: Person,
    derived: dict,
    source: Source,
    stats: dict,
    plugin: str | None,
) -> None:
    identifier_type = derived.get("type")
    value = (derived.get("value") or "").strip()
    if not identifier_type or not value:
        return
    try:
        identifier, created = ident_service.add_identifier(
            db,
            person,
            identifier_type=identifier_type,
            value=value,
            platform_name=derived.get("platform"),
            confidence=float(derived.get("confidence", 0.4)),
            status=derived.get("status", VerificationStatus.HYPOTHESIS.value),
            source_id=source.id,
            actor=plugin,
        )
    except ValueError:
        return
    if created:
        stats["identifiers_created"] += 1
        stats["new_targets"].append(
            {
                "type": identifier_type,
                "value": value,
                "normalized": identifier.normalized_value,
                "confidence": identifier.confidence,
            }
        )


def _get_or_create_source(db: Session, run: PluginRun, item: dict) -> Source:
    ref = item.get("source", {})
    url = ref.get("url")
    raw_reference = ref.get("raw_reference")

    query = select(Source).where(Source.investigation_id == run.investigation_id)
    query = query.where(Source.url == url) if url else query.where(
        Source.raw_reference == raw_reference
    )
    existing = db.execute(query.where(Source.plugin == run.plugin)).scalars().first()
    if existing:
        existing.date_checked = _now()
        return existing

    source = Source(
        investigation_id=run.investigation_id,
        kind=ref.get("kind", "tool_output"),
        url=url,
        title=(ref.get("title") or "")[:500] or None,
        description=ref.get("description"),
        plugin=run.plugin,
        plugin_run_id=run.id,
        raw_reference=raw_reference,
        reliability=float(ref.get("reliability", 0.5)),
        date_discovered=_now(),
        date_checked=_now(),
    )
    db.add(source)
    db.flush()
    return source


def _get_or_create_finding(
    db: Session,
    run: PluginRun,
    item: dict,
    source: Source,
    person: Person | None,
) -> tuple[Finding, bool]:
    dedup_key = item.get("dedup_key")
    if dedup_key:
        existing = db.execute(
            select(Finding).where(
                Finding.investigation_id == run.investigation_id,
                Finding.person_id == (person.id if person else None),
                Finding.type == item["kind"],
                Finding.dedup_key == dedup_key,
            )
        ).scalars().first()
        if existing:
            # A finding already rejected by an analyst is never resurrected.
            if existing.status not in {
                FindingStatus.REJECTED.value,
                FindingStatus.CONFIRMED.value,
            }:
                existing.confidence = max(existing.confidence, item.get("confidence", 0.0))
                existing.content = item["payload"]
            return existing, False

    finding = Finding(
        investigation_id=run.investigation_id,
        person_id=person.id if person else None,
        type=item["kind"],
        title=item["title"][:500],
        content=item["payload"],
        dedup_key=dedup_key,
        source_id=source.id,
        plugin=run.plugin,
        plugin_run_id=run.id,
        confidence=float(item.get("confidence", 0.5)),
        status=FindingStatus.NEW.value,
        discovered_at=_now(),
    )
    db.add(finding)
    db.flush()
    return finding, True


#: `findings.dedup_key` and `search_results.dedup_key` are varchar(300). Some
#: plugins build keys from a whole search query, which can exceed that; a
#: bounded hash keeps them unique without truncating them into collisions.
MAX_DEDUP_KEY = 300


def bounded_dedup_key(value: str | None) -> str | None:
    """Return a dedup key guaranteed to fit the column, and still unique."""
    if not value or len(value) <= MAX_DEDUP_KEY:
        return value
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:32]
    prefix = value[: MAX_DEDUP_KEY - len(digest) - 1]
    return f"{prefix}:{digest}"


def _coerce(item: dict) -> dict:
    """Fill in the missing fields of an item coming off the Celery bus."""
    payload = item.get("payload") or {}
    kind = item.get("kind") or FindingType.OTHER.value
    title = item.get("title") or kind
    dedup_key = bounded_dedup_key(item.get("dedup_key"))
    if not dedup_key:
        url = payload.get("url")
        dedup_key = (
            bounded_dedup_key(f"{kind}:{normalize(IdentifierType.WEBSITE.value, url)}")
            if url
            else None
        )
    return {
        "kind": kind,
        "title": title,
        "payload": payload,
        "source": item.get("source") or {},
        "confidence": item.get("confidence", 0.5),
        "dedup_key": dedup_key,
        "derived_identifiers": item.get("derived_identifiers") or [],
        "warnings": item.get("warnings") or [],
    }


def find_person(db: Session, person_id: uuid.UUID | None) -> Person | None:
    return db.get(Person, person_id) if person_id else None


def import_search_into_person(db: Session, search, person: Person) -> dict:
    """Re-attach a search and everything it produced to a person.

    A quick search runs without a person, so its findings sit at case-file level
    with nobody to correlate them against. Rather than re-querying the third
    party services - which the differential search exists to avoid - this moves
    the stored results across and replays the correlation step, so profiles,
    usernames, identifiers and the score are rebuilt for the target person.
    """
    from app.models.evidence import Source
    from app.models.ops import PluginRun
    from app.services.correlation import recompute_person_score

    if search.person_id == person.id:
        raise ValueError("this search is already attached to that person")

    stats = {
        "findings_moved": 0,
        "profiles_created": 0,
        "identifiers_created": 0,
        "sources_moved": 0,
        "runs_moved": 0,
    }
    target_investigation = person.investigation_id

    runs = list(
        db.execute(select(PluginRun).where(PluginRun.search_id == search.id)).scalars().all()
    )

    for run in runs:
        run.person_id = person.id
        run.investigation_id = target_investigation
        stats["runs_moved"] += 1

        findings = list(
            db.execute(select(Finding).where(Finding.plugin_run_id == run.id)).scalars().all()
        )
        for finding in findings:
            finding.person_id = person.id
            finding.investigation_id = target_investigation
            stats["findings_moved"] += 1

            source = db.get(Source, finding.source_id) if finding.source_id else None
            if source is not None and source.investigation_id != target_investigation:
                source.investigation_id = target_investigation
                stats["sources_moved"] += 1

            # Replay the correlation a person-less search could not perform.
            if finding.type in PROFILE_KINDS and source is not None:
                item = {
                    "kind": finding.type,
                    "title": finding.title,
                    "payload": finding.content or {},
                    "confidence": finding.confidence,
                    "dedup_key": finding.dedup_key,
                }
                _ingest_profile(db, run, person, item, source, finding, stats)

    search.person_id = person.id
    search.investigation_id = target_investigation
    db.flush()

    recompute_person_score(db, person)
    ident_service.add_timeline(
        db,
        person,
        kind="search_imported",
        message=(
            f"Results imported from search {search.target_type} = {search.target_value}: "
            f"{stats['findings_moved']} finding(s)"
        ),
        payload={"search_id": str(search.id), **stats},
    )
    return stats
