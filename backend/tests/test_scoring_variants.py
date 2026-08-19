"""Explainable scoring and variant generation (hypotheses)."""
from __future__ import annotations

from app.models.enums import VerificationStatus
from app.services.scoring import case_file_score, score_identity_match
from app.services.variants import combined, from_name, from_username


def test_username_alone_is_not_an_identity() -> None:
    """An identical username alone must never be enough to conclude."""
    score = score_identity_match({"username_match": "jdupont"})
    assert score.total < 25
    assert score.verdict == "INSUFFICIENT"
    assert any(c.code == "single_weak_signal" for c in score.contributions)


def test_email_plus_name_plus_location_is_strong() -> None:
    score = score_identity_match(
        {
            "email_match": "jean@exemple.fr",
            "name_match": "jean dupont",
            "location_match": "bethune",
            "username_match": "jdupont",
        },
        source_reliability=0.9,
    )
    assert score.total >= 75
    assert score.verdict == "STRONG_MATCH"
    labels = {c.code for c in score.contributions}
    assert {"email_match", "name_match", "location_match", "reliable_source"} <= labels


def test_contradiction_lowers_score() -> None:
    with_conflict = score_identity_match(
        {"username_match": "x", "name_match": "y", "location_conflict": "Lille vs Bethune"}
    )
    without = score_identity_match({"username_match": "x", "name_match": "y"})
    assert with_conflict.total < without.total


def test_human_decision_overrides_verdict() -> None:
    rejected = score_identity_match(
        {"email_match": "a@b.fr", "name_match": "x"},
        human_status=VerificationStatus.REJECTED.value,
    )
    assert rejected.verdict == "REJECTED"

    confirmed = score_identity_match(
        {}, human_status=VerificationStatus.CONFIRMED.value
    )
    assert confirmed.verdict == "CONFIRMED"


def test_score_is_always_explainable() -> None:
    payload = score_identity_match({"email_match": "a@b.fr"}).as_dict()
    assert payload["breakdown"]
    assert all({"code", "label", "points"} <= set(item) for item in payload["breakdown"])
    assert "hypothesis" in payload["disclaimer"].lower()


def test_case_file_score_penalises_open_contradictions() -> None:
    clean = case_file_score(
        confirmed_identifiers=4, total_identifiers=5,
        confirmed_profiles=2, total_profiles=3,
        distinct_sources=6, contradictions=0,
    )
    messy = case_file_score(
        confirmed_identifiers=4, total_identifiers=5,
        confirmed_profiles=2, total_profiles=3,
        distinct_sources=6, contradictions=3,
    )
    assert messy.total < clean.total


def test_variants_from_name_include_expected_forms() -> None:
    values = {v.value for v in from_name("Jean", "Dupont")}
    assert {"jeandupont", "jean.dupont", "j.dupont", "dupontjean"} <= values


def test_variants_are_always_hypotheses() -> None:
    for variant in from_username("jdupont"):
        assert variant.confidence <= 0.6
        assert variant.as_dict()["status"] == "HYPOTHESIS"


def test_combined_excludes_known_usernames() -> None:
    suggestions = combined("Jean", "Dupont", known_usernames=["jdupont"], limit=50)
    assert "jdupont" not in {s.value for s in suggestions}


def test_variants_are_bounded() -> None:
    suggestions = combined("Jean", "Dupont", known_usernames=["jdupont"], limit=10)
    assert len(suggestions) <= 10
    assert all(3 <= len(s.value) <= 40 for s in suggestions)
