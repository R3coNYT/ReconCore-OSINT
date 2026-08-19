"""Explainable confidence engine.

Every score is the sum of named contributions, positive or negative. No score is
produced without its justification: the API always returns the breakdown, and
the interface renders it as-is.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.models.enums import SourceKind, VerificationStatus

#: Default reliability of a source, per category (configurable in the database).
SOURCE_RELIABILITY: dict[str, float] = {
    SourceKind.OFFICIAL_API.value: 1.00,
    SourceKind.OFFICIAL_WEBSITE.value: 0.95,
    SourceKind.VERIFIED_SOURCE.value: 0.90,
    SourceKind.ESTABLISHED_DATABASE.value: 0.80,
    SourceKind.TOOL_OUTPUT.value: 0.70,
    SourceKind.SEARCH_ENGINE.value: 0.65,
    SourceKind.MANUAL_ENTRY.value: 0.60,
    SourceKind.UNVERIFIED_WEBSITE.value: 0.40,
    SourceKind.USER_HYPOTHESIS.value: 0.20,
}

#: Weight of each identity-match signal (points out of 100).
SIGNAL_WEIGHTS: dict[str, int] = {
    "email_match": 25,
    "username_match": 20,
    "name_match": 15,
    "phone_match": 22,
    "location_match": 10,
    "bio_match": 12,
    "external_url_match": 12,
    "organization_match": 10,
    "avatar_match": 10,
    "platform_id_match": 18,
    "reliable_source": 5,
    "human_confirmed": 30,
}

#: Penalties applied for contradictions and rejections.
PENALTY_WEIGHTS: dict[str, int] = {
    "location_conflict": -12,
    "name_conflict": -15,
    "email_conflict": -20,
    "human_rejected": -60,
    "variant_only": -10,
    "single_weak_signal": -5,
}


@dataclass
class ScoreContribution:
    code: str
    label: str
    points: int
    detail: str | None = None

    def as_dict(self) -> dict:
        return {
            "code": self.code,
            "label": self.label,
            "points": self.points,
            "detail": self.detail,
        }


@dataclass
class Score:
    """A 0..100 score together with its full justification."""

    contributions: list[ScoreContribution] = field(default_factory=list)

    def add(self, code: str, label: str, points: int, detail: str | None = None) -> None:
        self.contributions.append(ScoreContribution(code, label, points, detail))

    @property
    def total(self) -> int:
        return max(0, min(100, sum(c.points for c in self.contributions)))

    @property
    def ratio(self) -> float:
        return round(self.total / 100.0, 4)

    @property
    def verdict(self) -> str:
        """Deliberately cautious wording: never "confirmed" without a human."""
        if any(c.code == "human_confirmed" for c in self.contributions):
            return "CONFIRMED"
        if any(c.code == "human_rejected" for c in self.contributions):
            return "REJECTED"
        total = self.total
        if total >= 75:
            return "STRONG_MATCH"
        if total >= 50:
            return "POSSIBLE_MATCH"
        if total >= 25:
            return "WEAK_SIGNAL"
        return "INSUFFICIENT"

    def as_dict(self) -> dict:
        return {
            "score": self.total,
            "ratio": self.ratio,
            "verdict": self.verdict,
            "breakdown": [c.as_dict() for c in self.contributions],
            "disclaimer": (
                "Indicative score computed from the available signals. A strong "
                "match remains a hypothesis until an analyst confirms it."
            ),
        }


LABELS = {
    "email_match": "Matching email",
    "username_match": "Matching username",
    "name_match": "Matching name",
    "phone_match": "Matching phone number",
    "location_match": "Consistent location",
    "bio_match": "Consistent bio",
    "external_url_match": "Shared external link",
    "organization_match": "Consistent organisation",
    "avatar_match": "Similar profile picture",
    "platform_id_match": "Matching platform user ID",
    "reliable_source": "Reliable source",
    "human_confirmed": "Confirmed by an analyst",
    "location_conflict": "Conflicting locations",
    "name_conflict": "Conflicting names",
    "email_conflict": "Conflicting emails",
    "human_rejected": "Rejected by an analyst",
    "variant_only": "Relies solely on a hypothetical variant",
    "single_weak_signal": "Single weak signal",
}


def score_identity_match(
    signals: dict[str, bool | str | None],
    *,
    source_reliability: float = 0.5,
    human_status: str | None = None,
) -> Score:
    """Score how well a discovered profile matches a person.

    `signals` maps a signal code to True or to a descriptive value. Only codes
    known to SIGNAL_WEIGHTS / PENALTY_WEIGHTS are taken into account.
    """
    score = Score()

    for code, weight in SIGNAL_WEIGHTS.items():
        if code in {"human_confirmed", "reliable_source"}:
            continue
        value = signals.get(code)
        if value:
            detail = value if isinstance(value, str) else None
            score.add(code, LABELS.get(code, code), weight, detail)

    for code, penalty in PENALTY_WEIGHTS.items():
        if code in {"human_rejected"}:
            continue
        if signals.get(code):
            value = signals.get(code)
            detail = value if isinstance(value, str) else None
            score.add(code, LABELS.get(code, code), penalty, detail)

    if source_reliability >= 0.8:
        score.add(
            "reliable_source",
            LABELS["reliable_source"],
            SIGNAL_WEIGHTS["reliable_source"],
            f"reliability {source_reliability:.2f}",
        )

    if human_status == VerificationStatus.CONFIRMED.value:
        score.add("human_confirmed", LABELS["human_confirmed"], SIGNAL_WEIGHTS["human_confirmed"])
    elif human_status == VerificationStatus.REJECTED.value:
        score.add("human_rejected", LABELS["human_rejected"], PENALTY_WEIGHTS["human_rejected"])

    positives = [c for c in score.contributions if c.points > 0]
    if len(positives) == 1 and positives[0].points <= 20:
        score.add(
            "single_weak_signal",
            LABELS["single_weak_signal"],
            PENALTY_WEIGHTS["single_weak_signal"],
            "an identical username does not prove an identity",
        )

    return score


def reliability_for(kind: str, override: dict[str, float] | None = None) -> float:
    table = {**SOURCE_RELIABILITY, **(override or {})}
    return table.get(kind, 0.5)


def case_file_score(
    *,
    confirmed_identifiers: int,
    total_identifiers: int,
    confirmed_profiles: int,
    total_profiles: int,
    distinct_sources: int,
    contradictions: int,
) -> Score:
    """Consolidation score for a case file (quality, not volume)."""
    score = Score()
    if total_identifiers:
        ratio = confirmed_identifiers / total_identifiers
        score.add(
            "identifiers_confirmed",
            "Confirmed identifiers",
            int(30 * ratio),
            f"{confirmed_identifiers}/{total_identifiers}",
        )
    if total_profiles:
        ratio = confirmed_profiles / total_profiles
        score.add(
            "profiles_confirmed",
            "Confirmed profiles",
            int(30 * ratio),
            f"{confirmed_profiles}/{total_profiles}",
        )
    score.add(
        "source_diversity",
        "Source diversity",
        min(25, distinct_sources * 3),
        f"{distinct_sources} distinct sources",
    )
    if contradictions:
        score.add(
            "open_contradictions",
            "Unresolved contradictions",
            -min(30, contradictions * 8),
            f"{contradictions} contradiction(s)",
        )
    return score
