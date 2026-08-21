"""Keeping one analyst decision consistent across every view of the same fact.

A single tool result is stored as up to four rows: a `Finding` (the evidence
tab), a `SocialProfile` (the profiles tab), a `Username` (the usernames tab)
and an `Identifier` (the identifiers tab). They are separate rows because each
view needs different columns - not because they are separate facts.

Until now each row carried its own status, so rejecting a finding left the
account it describes sitting at HYPOTHESIS in three other tabs, and the
overview kept counting it as pending. The dashboard contradicted the decision
the analyst had just made.

The join is `source_id`: everything `ingest.py` creates out of one normalized
item carries the id of the `Source` that produced it, and sources are deduped
per (investigation, url, plugin), so one source means one observed fact.

Two deliberate limits:

* A row with no source - a manual entry added without a source URL - cannot be
  linked to anything, so it is left alone rather than matched by name.
* Propagation is a decision, not a recomputation: it only runs when a human
  acts. A later plugin run still cannot overwrite a decided row (see
  `DECIDED_STATUSES` in `ingest.py`).
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.enums import FindingStatus, VerificationStatus
from app.models.evidence import Finding
from app.models.identity import Identifier, SocialProfile, Username

#: How an evidence decision reads for the account it documents.
#:
#: OUTDATED and CONTRADICTED have no equivalent in `VerificationStatus`. Both
#: mean "this no longer stands as established", which is HYPOTHESIS - the
#: detail stays readable on the finding itself rather than being flattened
#: into a status that would claim more than we know.
FINDING_TO_VERIFICATION = {
    FindingStatus.CONFIRMED.value: VerificationStatus.CONFIRMED.value,
    FindingStatus.PROBABLE.value: VerificationStatus.PROBABLE.value,
    FindingStatus.REJECTED.value: VerificationStatus.REJECTED.value,
    FindingStatus.UNVERIFIED.value: VerificationStatus.HYPOTHESIS.value,
    FindingStatus.OUTDATED.value: VerificationStatus.HYPOTHESIS.value,
    FindingStatus.CONTRADICTED.value: VerificationStatus.HYPOTHESIS.value,
    FindingStatus.NEW.value: VerificationStatus.HYPOTHESIS.value,
}

#: And the other way round. Sending an account back to HYPOTHESIS or UNKNOWN
#: is still a human act, so the finding becomes UNVERIFIED ("under review")
#: rather than NEW ("never looked at").
VERIFICATION_TO_FINDING = {
    VerificationStatus.CONFIRMED.value: FindingStatus.CONFIRMED.value,
    VerificationStatus.PROBABLE.value: FindingStatus.PROBABLE.value,
    VerificationStatus.REJECTED.value: FindingStatus.REJECTED.value,
    VerificationStatus.HYPOTHESIS.value: FindingStatus.UNVERIFIED.value,
    VerificationStatus.UNKNOWN.value: FindingStatus.UNVERIFIED.value,
}

#: Confidence follows the decision. Anything else keeps the computed score:
#: "probable" is precisely the case where the number still carries meaning.
CONFIDENCE_BY_STATUS = {
    VerificationStatus.CONFIRMED.value: 1.0,
    VerificationStatus.REJECTED.value: 0.0,
}

#: What `propagate` reports back, in the order the analyst sees the tabs.
_LABELS = ("findings", "profiles", "usernames", "identifiers")


def propagate(
    db: Session,
    *,
    person_id: uuid.UUID,
    source_id: uuid.UUID | None,
    verification: str,
    user_id: uuid.UUID | None = None,
    skip: tuple[str, uuid.UUID] | None = None,
) -> dict[str, int]:
    """Align every row born from `source_id` with one human decision.

    `skip` names the row the analyst acted on (``("profile", id)``), which the
    caller has already updated with its own view-specific rules.

    Returns the number of rows changed per kind; rows already carrying the
    target status are not counted, so an unchanged result means the views were
    already consistent.
    """
    counts = dict.fromkeys(_LABELS, 0)
    if source_id is None:
        # Manual entry with no source: nothing to join on.
        return counts

    verification = VerificationStatus(verification).value
    finding_status = VERIFICATION_TO_FINDING[verification]
    confidence = CONFIDENCE_BY_STATUS.get(verification)
    now = datetime.now(UTC)

    for finding in _rows(db, Finding, person_id, source_id, skip, "finding"):
        if finding.status == finding_status:
            continue
        finding.status = finding_status
        finding.verified_at = now
        finding.verified_by_id = user_id
        if confidence is not None:
            finding.confidence = confidence
        counts["findings"] += 1

    for profile in _rows(db, SocialProfile, person_id, source_id, skip, "profile"):
        if profile.status == verification:
            continue
        profile.status = verification
        if confidence is not None:
            profile.confidence = confidence
        counts["profiles"] += 1

    for username in _rows(db, Username, person_id, source_id, skip, "username"):
        if username.status == verification:
            continue
        username.status = verification
        if confidence is not None:
            username.confidence = confidence
        if verification == VerificationStatus.CONFIRMED.value:
            # A confirmed username is no longer a generated hypothesis.
            username.is_variant = False
        counts["usernames"] += 1

    for identifier in _rows(db, Identifier, person_id, source_id, skip, "identifier"):
        if identifier.status == verification:
            continue
        identifier.status = verification
        if confidence is not None:
            identifier.confidence = confidence
        counts["identifiers"] += 1

    db.flush()
    return counts


def propagate_from_finding(
    db: Session,
    finding: Finding,
    *,
    user_id: uuid.UUID | None = None,
) -> dict[str, int]:
    """Mirror a finding decision onto the account it documents."""
    if finding.person_id is None:
        return dict.fromkeys(_LABELS, 0)
    return propagate(
        db,
        person_id=finding.person_id,
        source_id=finding.source_id,
        verification=FINDING_TO_VERIFICATION[finding.status],
        user_id=user_id,
        skip=("finding", finding.id),
    )


def backfill(db: Session, *, dry_run: bool = False) -> dict[str, int]:
    """Align rows that were decided before propagation existed.

    Findings are treated as the source of truth: they are the only rows that
    record who decided and when, so a divergence is resolved in their favour.

    Findings are replayed oldest decision first, so that when two of them share
    one source the most recent decision is the one that stands.
    """
    totals = dict.fromkeys(_LABELS, 0)
    decided = (
        db.execute(
            select(Finding)
            .where(
                Finding.status != FindingStatus.NEW.value,
                Finding.source_id.is_not(None),
                Finding.person_id.is_not(None),
            )
            .order_by(Finding.verified_at.asc().nulls_first())
        )
        .scalars()
        .all()
    )
    for finding in decided:
        counts = propagate_from_finding(db, finding, user_id=finding.verified_by_id)
        for key, moved in counts.items():
            totals[key] += moved

    if dry_run:
        db.rollback()
    totals["examined"] = len(decided)
    return totals


def summarize(counts: dict[str, int]) -> str:
    """Human-readable tail for a timeline entry, empty when nothing moved."""
    moved = [f"{n} {label}" for label, n in counts.items() if n and label in _LABELS]
    return f" - also updated: {', '.join(moved)}" if moved else ""


def _rows(
    db: Session,
    model: type,
    person_id: uuid.UUID,
    source_id: uuid.UUID,
    skip: tuple[str, uuid.UUID] | None,
    kind: str,
):
    query = select(model).where(
        model.person_id == person_id, model.source_id == source_id
    )
    rows = db.execute(query).scalars().all()
    if skip is not None and skip[0] == kind:
        return [row for row in rows if row.id != skip[1]]
    return rows
