"""Identifier normalisation.

The normalised form is used for comparison, deduplication and correlation. It
never replaces the value as entered: both are stored.
"""
from __future__ import annotations

import re
import unicodedata
from urllib.parse import urlparse

import phonenumbers
from phonenumbers import NumberParseException

from app.models.enums import IdentifierType

#: Default region for national numbers (e.g. "0612345678").
DEFAULT_REGION = "FR"

_WS = re.compile(r"\s+")
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def strip_accents(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def normalize_text(value: str) -> str:
    return _WS.sub(" ", strip_accents(value).strip().lower())


def normalize_name(value: str) -> str:
    """Comparable name: accent-free, lower-cased, punctuation collapsed."""
    cleaned = _NON_ALNUM.sub(" ", strip_accents(value).lower())
    return _WS.sub(" ", cleaned).strip()


def normalize_username(value: str) -> str:
    """Pseudo comparable.

    Separators (`.`, `_`, `-`) are stripped because most platforms treat them
    as equivalent or optional. The original value is still stored as-is so
    profile URLs can be built from it.
    """
    v = strip_accents(value).strip().lower().lstrip("@")
    if v.startswith("u/") or v.startswith("@"):
        v = v.split("/", 1)[-1]
    return re.sub(r"[._\-\s]", "", v)


def normalize_email(value: str) -> str:
    v = value.strip().lower()
    if "@" not in v:
        return v
    local, _, domain = v.rpartition("@")
    # `+tag` aliases point at the same mailbox: they are neutralised for
    # comparison only (the analyst still sees the original value).
    local = local.split("+", 1)[0]
    if domain in {"gmail.com", "googlemail.com"}:
        local = local.replace(".", "")
        domain = "gmail.com"
    return f"{local}@{domain}"


def email_parts(value: str) -> tuple[str, str]:
    local, _, domain = value.strip().lower().rpartition("@")
    return local, domain


def normalize_phone(value: str, region: str = DEFAULT_REGION) -> str:
    """Return the E.164 form (`+33612345678`), or the cleaned value."""
    raw = value.strip()
    try:
        parsed = phonenumbers.parse(raw, None if raw.startswith("+") else region)
    except NumberParseException:
        return re.sub(r"[^0-9+]", "", raw)
    if not phonenumbers.is_possible_number(parsed):
        return re.sub(r"[^0-9+]", "", raw)
    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)


def phone_metadata(value: str, region: str = DEFAULT_REGION) -> dict:
    """Public metadata for a number (country, line type, carrier, formats)."""
    from phonenumbers import carrier, geocoder
    from phonenumbers import timezone as pn_timezone

    try:
        parsed = phonenumbers.parse(
            value, None if value.strip().startswith("+") else region
        )
    except NumberParseException as exc:
        return {"valid": False, "error": str(exc)}
    number_type = phonenumbers.number_type(parsed)
    type_names = {
        phonenumbers.PhoneNumberType.MOBILE: "mobile",
        phonenumbers.PhoneNumberType.FIXED_LINE: "fixed_line",
        phonenumbers.PhoneNumberType.FIXED_LINE_OR_MOBILE: "fixed_line_or_mobile",
        phonenumbers.PhoneNumberType.VOIP: "voip",
        phonenumbers.PhoneNumberType.TOLL_FREE: "toll_free",
        phonenumbers.PhoneNumberType.PREMIUM_RATE: "premium_rate",
    }
    return {
        "valid": phonenumbers.is_valid_number(parsed),
        "possible": phonenumbers.is_possible_number(parsed),
        "e164": phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164),
        "international": phonenumbers.format_number(
            parsed, phonenumbers.PhoneNumberFormat.INTERNATIONAL
        ),
        "national": phonenumbers.format_number(
            parsed, phonenumbers.PhoneNumberFormat.NATIONAL
        ),
        "country_code": parsed.country_code,
        "region": phonenumbers.region_code_for_number(parsed),
        "location": geocoder.description_for_number(parsed, "fr"),
        "carrier": carrier.name_for_number(parsed, "fr"),
        "timezones": list(pn_timezone.time_zones_for_number(parsed)),
        "type": type_names.get(number_type, "unknown"),
    }


def normalize_domain(value: str) -> str:
    v = value.strip().lower()
    if "://" in v:
        v = urlparse(v).netloc or v
    v = v.split("/", 1)[0].split(":", 1)[0]
    return v[4:] if v.startswith("www.") else v


def normalize_url(value: str) -> str:
    v = value.strip()
    if not v:
        return v
    if "://" not in v:
        v = f"https://{v}"
    parsed = urlparse(v)
    netloc = parsed.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    path = parsed.path.rstrip("/")
    return f"{parsed.scheme}://{netloc}{path}"


_NORMALIZERS = {
    IdentifierType.EMAIL: normalize_email,
    IdentifierType.PHONE: normalize_phone,
    IdentifierType.USERNAME: normalize_username,
    IdentifierType.ALIAS: normalize_username,
    IdentifierType.DOMAIN: normalize_domain,
    IdentifierType.WEBSITE: normalize_url,
    IdentifierType.SOCIAL_PROFILE: normalize_url,
    IdentifierType.NAME: normalize_name,
    IdentifierType.FIRST_NAME: normalize_name,
    IdentifierType.LAST_NAME: normalize_name,
    IdentifierType.CITY: normalize_name,
    IdentifierType.DEPARTMENT: normalize_name,
    IdentifierType.REGION: normalize_name,
    IdentifierType.COUNTRY: normalize_name,
    IdentifierType.COMPANY: normalize_name,
    IdentifierType.ORGANIZATION: normalize_name,
}


def normalize(identifier_type: str, value: str) -> str:
    """Single entry point: normalise according to the identifier type."""
    try:
        key = IdentifierType(identifier_type)
    except ValueError:
        return normalize_text(value)
    fn = _NORMALIZERS.get(key, normalize_text)
    return fn(value)


def extract_username_from_url(url: str) -> str | None:
    """Infer a username from a common profile URL."""
    try:
        parsed = urlparse(url if "://" in url else f"https://{url}")
    except ValueError:
        return None
    segments = [s for s in parsed.path.split("/") if s]
    if not segments:
        return None
    candidate = segments[-1] if segments[0] in {"u", "user", "users", "in"} else segments[0]
    candidate = candidate.lstrip("@")
    if not candidate or "." in candidate and candidate.count(".") > 2:
        return None
    return candidate


EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
URL_RE = re.compile(r"https?://[^\s<>\"')]+")


def extract_emails(text: str) -> list[str]:
    return sorted({m.group(0).lower() for m in EMAIL_RE.finditer(text or "")})


def extract_urls(text: str) -> list[str]:
    return sorted({m.group(0).rstrip(".,);") for m in URL_RE.finditer(text or "")})
