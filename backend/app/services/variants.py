"""Username variant generation.

IMPORTANT: a variant is a SEARCH HYPOTHESIS. It is never automatically
attributed to the person; it is flagged `is_variant=True` with a `HYPOTHESIS`
status and low confidence, and must be confirmed by a human or by converging
evidence.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.services.normalization import normalize_username, strip_accents

SEPARATORS = ("", ".", "_", "-")


@dataclass(frozen=True)
class UsernameVariant:
    value: str
    rule: str
    confidence: float

    def as_dict(self) -> dict:
        return {
            "value": self.value,
            "rule": self.rule,
            "confidence": self.confidence,
            "status": "HYPOTHESIS",
        }


def _clean(part: str) -> str:
    return "".join(ch for ch in strip_accents(part).lower() if ch.isalnum())


def from_name(
    first_name: str | None, last_name: str | None, birth_year: int | None = None
) -> list[UsernameVariant]:
    """Variants derived from a first name and a last name."""
    first, last = _clean(first_name or ""), _clean(last_name or "")
    out: list[UsernameVariant] = []
    if not first and not last:
        return out

    if first and last:
        fi = first[0]
        for sep in SEPARATORS:
            out.append(UsernameVariant(f"{first}{sep}{last}", "first_last", 0.35))
            out.append(UsernameVariant(f"{last}{sep}{first}", "last_first", 0.30))
            out.append(UsernameVariant(f"{fi}{sep}{last}", "initial_last", 0.30))
            out.append(UsernameVariant(f"{last}{sep}{fi}", "last_initial", 0.25))
        out.append(UsernameVariant(f"{first}{last[0]}", "first_initial", 0.25))
    elif first:
        out.append(UsernameVariant(first, "first_only", 0.20))
    elif last:
        out.append(UsernameVariant(last, "last_only", 0.20))

    if birth_year:
        base = f"{first}{last}" if first and last else (first or last)
        yy = str(birth_year)[-2:]
        out.append(UsernameVariant(f"{base}{birth_year}", "with_birth_year", 0.20))
        out.append(UsernameVariant(f"{base}{yy}", "with_birth_year_short", 0.20))

    return _dedupe(out)


def from_username(username: str, extra_numbers: list[str] | None = None) -> list[UsernameVariant]:
    """Variants derived from a known username (separators, common suffixes)."""
    base = normalize_username(username)
    if not base:
        return []

    out: list[UsernameVariant] = [UsernameVariant(base, "canonical", 0.60)]

    # Re-insert separators at letter/digit boundaries.
    boundary = _split_alpha_digit(base)
    if len(boundary) > 1:
        for sep in (".", "_", "-"):
            out.append(
                UsernameVariant(sep.join(boundary), f"separator_{sep}", 0.30)
            )

    # Common numeric suffixes (area code, year, disambiguation).
    for number in extra_numbers or []:
        digits = "".join(ch for ch in str(number) if ch.isdigit())
        if digits:
            out.append(UsernameVariant(f"{base}{digits}", "numeric_suffix", 0.25))
            out.append(UsernameVariant(f"{base}_{digits}", "numeric_suffix_sep", 0.22))

    for suffix in ("official", "real", "off", "_", "1", "01", "x"):
        out.append(UsernameVariant(f"{base}{suffix}", "common_suffix", 0.18))
    for prefix in ("the", "im", "its"):
        out.append(UsernameVariant(f"{prefix}{base}", "common_prefix", 0.15))

    return _dedupe(out)


def combined(
    first_name: str | None,
    last_name: str | None,
    known_usernames: list[str] | None = None,
    birth_year: int | None = None,
    location_codes: list[str] | None = None,
    limit: int = 60,
) -> list[UsernameVariant]:
    """Every username hypothesis for a person, ranked by confidence."""
    variants = from_name(first_name, last_name, birth_year)
    for username in known_usernames or []:
        variants.extend(from_username(username, extra_numbers=location_codes))
    known = {normalize_username(u) for u in (known_usernames or [])}
    ranked = sorted(_dedupe(variants), key=lambda v: -v.confidence)
    return [v for v in ranked if v.value not in known][:limit]


def _split_alpha_digit(value: str) -> list[str]:
    parts: list[str] = []
    current = ""
    for ch in value:
        if current and ch.isdigit() != current[-1].isdigit():
            parts.append(current)
            current = ch
        else:
            current += ch
    if current:
        parts.append(current)
    return parts


def _dedupe(variants: list[UsernameVariant]) -> list[UsernameVariant]:
    best: dict[str, UsernameVariant] = {}
    for variant in variants:
        if len(variant.value) < 3 or len(variant.value) > 40:
            continue
        existing = best.get(variant.value)
        if existing is None or variant.confidence > existing.confidence:
            best[variant.value] = variant
    return list(best.values())
