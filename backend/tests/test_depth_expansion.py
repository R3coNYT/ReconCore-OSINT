"""Depth expansion must not invent targets.

Regression test for a runaway found in production: searching the username
`ARG_R3coN` at depth 2 launched full several-hundred-site scans for `photos`,
`wiki`, `profile`, `hackers`, `perfil`... Those came from guessing a username
out of each discovered profile URL, which produced generic path segments and
roughly 5600 pointless requests to third-party services.
"""
from __future__ import annotations

import pytest

from app.models.enums import IdentifierType
from app.plugins import registry
from app.services import orchestration
from app.services.orchestration import MAX_NEXT_LEVEL_TASKS, next_level_tasks


@pytest.fixture
def enabled_plugins(db):
    """Expansion only considers enabled plugins, which are off by default."""
    registry.sync_registry(db)
    for name in ("holehe", "phoneinfoga"):
        registry.get_entry(db, name).enabled = True
    db.flush()
    yield
    for name in ("holehe", "phoneinfoga"):
        registry.get_entry(db, name).enabled = False
    db.flush()

#: Real URLs returned by Sherlock for the username ARG_R3coN.
PROFILE_URLS = [
    "https://www.mercadolivre.com.br/perfil/ARG_R3coN",
    "https://www.airliners.net/user/ARG_R3coN/profile/photos",
    "https://developer.apple.com/forums/profile/ARG_R3coN",
    "https://www.wikidot.com/user:info/ARG_R3coN",
    "https://pikabu.ru/@ARG_R3coN",
]


def test_profile_urls_never_become_search_targets(db) -> None:
    targets = [
        {"type": IdentifierType.SOCIAL_PROFILE.value, "value": url}
        for url in PROFILE_URLS
    ]
    steps = next_level_tasks(db, targets, depth=2)
    assert steps == [], (
        "a profile URL must not spawn a search: the username it contains is the "
        "one already searched, and the rest of the path is not a username"
    )


def test_explicit_identifiers_still_expand(db, enabled_plugins, monkeypatch) -> None:
    """Emails, phones and domains found inside a profile remain actionable."""
    monkeypatch.setattr(orchestration, "_recently_done", lambda *a, **k: False)

    steps = next_level_tasks(
        db,
        [
            {"type": IdentifierType.EMAIL.value, "value": "jean@exemple.fr"},
            {"type": IdentifierType.PHONE.value, "value": "+33612345678"},
        ],
        depth=2,
    )
    expanded = {(s["type"], s["normalized"]) for s in steps}
    assert (IdentifierType.EMAIL.value, "jean@exemple.fr") in expanded
    assert (IdentifierType.PHONE.value, "+33612345678") in expanded


def test_fan_out_is_capped(db, enabled_plugins, monkeypatch, caplog) -> None:
    """One run may not spawn an unbounded number of scans."""
    monkeypatch.setattr(orchestration, "_recently_done", lambda *a, **k: False)

    many = [
        {"type": IdentifierType.EMAIL.value, "value": f"user{i}@exemple.fr"}
        for i in range(MAX_NEXT_LEVEL_TASKS + 15)
    ]
    with caplog.at_level("WARNING"):
        steps = next_level_tasks(db, many, depth=2)

    assert len(steps) <= MAX_NEXT_LEVEL_TASKS
    # A cap that stays silent reads as "everything was covered".
    assert any("capped" in record.message for record in caplog.records)


def test_unactionable_types_are_ignored(db) -> None:
    steps = next_level_tasks(
        db,
        [
            {"type": IdentifierType.CITY.value, "value": "Bethune"},
            {"type": IdentifierType.NOTE.value, "value": "something"},
        ],
        depth=2,
    )
    assert steps == []
