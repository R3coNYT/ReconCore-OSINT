"""One decision, one status, in every tab.

Regression test for an inconsistency found in production: a finding moved to
REJECTED left the profile, username and identifier documenting the same
account sitting at HYPOTHESIS, so the overview kept counting as pending an
account the analyst had just rejected - and the reverse was true too.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.models.enums import (
    FindingStatus,
    FindingType,
    IdentifierType,
    SourceKind,
    VerificationStatus,
)
from app.models.evidence import Finding, Source
from app.models.identity import Identifier, SocialProfile, Username
from app.models.investigation import Person
from app.models.user import User
from app.services import decisions


@pytest.fixture
def subject(db, person) -> Person:
    """The `person` fixture speaks HTTP; propagation works on the model."""
    return db.get(Person, uuid.UUID(person["id"]))


@pytest.fixture
def analyst_user(db, analyst) -> User:
    return db.execute(
        select(User).where(User.email == "analyst@reconcore-demo.fr")
    ).scalar_one()


@pytest.fixture
def linked_row_set(db, subject):
    """The four rows one tool result produces, joined by their source."""
    person = subject
    source = Source(
        investigation_id=person.investigation_id,
        kind=SourceKind.TOOL_OUTPUT.value,
        url="https://github.com/jdupont",
        title="Sherlock - GitHub",
        plugin="sherlock",
        reliability=0.7,
    )
    db.add(source)
    db.flush()

    rows = {
        "finding": Finding(
            investigation_id=person.investigation_id,
            person_id=person.id,
            type=FindingType.SOCIAL_PROFILE.value,
            title="GitHub: jdupont",
            dedup_key="social:github:jdupont",
            source_id=source.id,
            plugin="sherlock",
            confidence=0.35,
            status=FindingStatus.NEW.value,
        ),
        "profile": SocialProfile(
            investigation_id=person.investigation_id,
            person_id=person.id,
            username="jdupont",
            url="https://github.com/jdupont",
            source_id=source.id,
            confidence=0.35,
            status=VerificationStatus.HYPOTHESIS.value,
        ),
        "username": Username(
            investigation_id=person.investigation_id,
            person_id=person.id,
            value="jdupont",
            normalized_value="jdupont",
            source_id=source.id,
            confidence=0.35,
            is_variant=True,
            status=VerificationStatus.HYPOTHESIS.value,
        ),
        "identifier": Identifier(
            investigation_id=person.investigation_id,
            person_id=person.id,
            type=IdentifierType.SOCIAL_PROFILE.value,
            value="https://github.com/jdupont",
            normalized_value="https://github.com/jdupont",
            source_id=source.id,
            confidence=0.35,
            status=VerificationStatus.HYPOTHESIS.value,
        ),
    }
    db.add_all(rows.values())
    db.flush()
    rows["source"] = source
    return rows


def test_rejecting_a_finding_rejects_the_account(db, linked_row_set) -> None:
    finding = linked_row_set["finding"]
    finding.status = FindingStatus.REJECTED.value

    counts = decisions.propagate_from_finding(db, finding)

    assert counts == {"findings": 0, "profiles": 1, "usernames": 1, "identifiers": 1}
    for key in ("profile", "username", "identifier"):
        row = linked_row_set[key]
        assert row.status == VerificationStatus.REJECTED.value, key
        # A rejected item must stop weighing on the score.
        assert row.confidence == 0.0, key


def test_confirming_a_profile_confirms_the_finding(db, linked_row_set, analyst_user) -> None:
    profile = linked_row_set["profile"]
    profile.status = VerificationStatus.CONFIRMED.value

    decisions.propagate(
        db,
        person_id=profile.person_id,
        source_id=profile.source_id,
        verification=profile.status,
        user_id=analyst_user.id,
        skip=("profile", profile.id),
    )

    finding = linked_row_set["finding"]
    assert finding.status == FindingStatus.CONFIRMED.value
    assert finding.confidence == 1.0
    assert finding.verified_by_id == analyst_user.id
    assert finding.verified_at is not None
    # A confirmed username is no longer a generated hypothesis.
    assert linked_row_set["username"].is_variant is False


def test_the_row_the_analyst_touched_is_left_alone(db, linked_row_set) -> None:
    """The caller already applied its own view-specific rules to it."""
    username = linked_row_set["username"]
    username.status = VerificationStatus.PROBABLE.value

    counts = decisions.propagate(
        db,
        person_id=username.person_id,
        source_id=username.source_id,
        verification=username.status,
        skip=("username", username.id),
    )

    assert counts["usernames"] == 0
    assert username.status == VerificationStatus.PROBABLE.value
    assert linked_row_set["profile"].status == VerificationStatus.PROBABLE.value
    assert linked_row_set["finding"].status == FindingStatus.PROBABLE.value


def test_probable_keeps_the_computed_confidence(db, linked_row_set) -> None:
    """"Probable" is exactly the case where the score still means something."""
    finding = linked_row_set["finding"]
    finding.status = FindingStatus.PROBABLE.value

    decisions.propagate_from_finding(db, finding)

    assert linked_row_set["profile"].confidence == 0.35


def test_an_unrelated_row_is_never_touched(db, linked_row_set, subject) -> None:
    """Propagation joins on the source, never on a name that merely matches."""
    other = SocialProfile(
        investigation_id=subject.investigation_id,
        person_id=subject.id,
        username="jdupont",  # same handle, different observation
        url="https://gitlab.com/jdupont",
        source_id=None,
        confidence=0.35,
        status=VerificationStatus.HYPOTHESIS.value,
    )
    db.add(other)
    db.flush()

    finding = linked_row_set["finding"]
    finding.status = FindingStatus.REJECTED.value
    decisions.propagate_from_finding(db, finding)

    assert other.status == VerificationStatus.HYPOTHESIS.value


def test_a_sourceless_row_propagates_nothing(db, subject) -> None:
    """A manual entry with no source has nothing to join on: leave it alone."""
    counts = decisions.propagate(
        db,
        person_id=subject.id,
        source_id=None,
        verification=VerificationStatus.CONFIRMED.value,
    )
    assert counts == {"findings": 0, "profiles": 0, "usernames": 0, "identifiers": 0}


def test_every_status_has_a_mapping_both_ways() -> None:
    """A status added to either enum must not silently break propagation."""
    for status in FindingStatus:
        assert status.value in decisions.FINDING_TO_VERIFICATION
    for status in VerificationStatus:
        assert status.value in decisions.VERIFICATION_TO_FINDING


def test_backfill_realigns_past_decisions(db, linked_row_set) -> None:
    """Rows decided before propagation existed keep their stale status."""
    finding = linked_row_set["finding"]
    finding.status = FindingStatus.REJECTED.value  # decided the old way
    db.flush()

    result = decisions.backfill(db)

    assert result["examined"] >= 1
    assert linked_row_set["profile"].status == VerificationStatus.REJECTED.value
    assert linked_row_set["identifier"].status == VerificationStatus.REJECTED.value


def test_backfill_is_idempotent(db, linked_row_set) -> None:
    finding = linked_row_set["finding"]
    finding.status = FindingStatus.REJECTED.value
    db.flush()

    decisions.backfill(db)
    second = decisions.backfill(db)

    assert second["profiles"] == 0
    assert second["usernames"] == 0
    assert second["identifiers"] == 0
