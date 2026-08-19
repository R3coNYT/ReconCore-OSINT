"""Normalisation: equivalent formats must converge."""
from __future__ import annotations

import pytest

from app.models.enums import IdentifierType
from app.services.normalization import (
    extract_emails,
    extract_username_from_url,
    normalize,
    normalize_domain,
    normalize_email,
    normalize_name,
    normalize_phone,
    normalize_url,
    normalize_username,
)


@pytest.mark.parametrize(
    "raw",
    [
        "06 12 34 56 78",
        "0612345678",
        "+33 6 12 34 56 78",
        "+33612345678",
        "06.12.34.56.78",
        "06-12-34-56-78",
    ],
)
def test_phone_formats_converge(raw: str) -> None:
    assert normalize_phone(raw) == "+33612345678"


def test_phone_keeps_foreign_number() -> None:
    assert normalize_phone("+1 202 555 0143") == "+12025550143"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("@JDupont", "jdupont"),
        ("j.dupont", "jdupont"),
        ("j_dupont", "jdupont"),
        ("j-dupont", "jdupont"),
        ("u/jdupont", "jdupont"),
        ("  JDupont  ", "jdupont"),
    ],
)
def test_username_variants_converge(raw: str, expected: str) -> None:
    assert normalize_username(raw) == expected


def test_email_gmail_dots_and_tags() -> None:
    assert normalize_email("Jean.Dupont+osint@Gmail.com") == "jeandupont@gmail.com"


def test_email_other_domain_keeps_dots() -> None:
    assert normalize_email("Jean.Dupont@Exemple.FR") == "jean.dupont@exemple.fr"


def test_name_strips_accents_and_punctuation() -> None:
    assert normalize_name("Jean-Édouard  DUPONT") == "jean edouard dupont"


def test_domain_and_url() -> None:
    assert normalize_domain("https://WWW.Exemple.fr/page?a=1") == "exemple.fr"
    assert normalize_url("www.Exemple.fr/page/") == "https://exemple.fr/page"


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://instagram.com/jd_official", "jd_official"),
        ("https://reddit.com/user/jdupont", "jdupont"),
        ("https://x.com/@jdupont", "jdupont"),
        ("https://linkedin.com/in/jean-dupont", "jean-dupont"),
    ],
)
def test_extract_username_from_url(url: str, expected: str) -> None:
    assert extract_username_from_url(url) == expected


def test_normalize_dispatch_by_type() -> None:
    assert normalize(IdentifierType.EMAIL.value, "A.B+x@Gmail.com") == "ab@gmail.com"
    assert normalize(IdentifierType.PHONE.value, "06 12 34 56 78") == "+33612345678"
    assert normalize(IdentifierType.USERNAME.value, "@Jean_62") == "jean62"


def test_extract_emails_from_text() -> None:
    text = "Contact : jean@exemple.fr ou secours@autre.com."
    assert extract_emails(text) == ["jean@exemple.fr", "secours@autre.com"]
