"""Attaching a quick search to a person after the fact.

A quick search runs with no person, so its findings sit at case-file level and
correlate against nobody. Importing must move them across and replay the
correlation instead of querying the third-party services again.
"""
from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from app.db.session import SessionLocal
from app.models.enums import RunStatus
from app.models.evidence import Finding
from app.models.identity import SocialProfile
from app.models.investigation import Person
from app.models.ops import PluginRun, Search
from app.plugins.base import NormalizedItem, SourceKind, SourceRef
from app.services.ingest import ingest_items

API = "/api/v1"


def _quick_search(investigation_id: uuid.UUID) -> uuid.UUID:
    """A person-less search, exactly what the quick-search pages produce."""
    with SessionLocal() as db:
        search = Search(
            investigation_id=investigation_id,
            person_id=None,
            target_type="EMAIL",
            target_value="jean.dupont@exemple.fr",
            status=RunStatus.SUCCESS.value,
        )
        db.add(search)
        db.flush()

        run = PluginRun(
            search_id=search.id,
            investigation_id=investigation_id,
            person_id=None,
            plugin="holehe",
            target_type="EMAIL",
            target_value="jean.dupont@exemple.fr",
            normalized_target="jean.dupont@exemple.fr",
            status=RunStatus.SUCCESS.value,
        )
        db.add(run)
        db.flush()

        items = [
            NormalizedItem(
                kind="profile_metadata",
                title="GitHub : jdupont",
                payload={
                    "platform": "GitHub",
                    "username": "jdupont",
                    "url": "https://github.com/jdupont",
                    "display_name": "Jean Dupont",
                },
                source=SourceRef(kind=SourceKind.TOOL_OUTPUT.value, reliability=0.7),
                confidence=0.5,
                dedup_key="social:github:jdupont",
            ),
            NormalizedItem(
                kind="account_exists",
                title="Spotify : account linked",
                payload={"service": "spotify", "result": "used"},
                source=SourceRef(kind=SourceKind.TOOL_OUTPUT.value, reliability=0.7),
                confidence=0.6,
                dedup_key="holehe:spotify:jean.dupont@exemple.fr",
            ),
        ]
        # person=None is the whole point: nothing to correlate against yet.
        ingest_items(db, run, [i.as_dict() for i in items], person=None)
        db.commit()
        return search.id


def test_quick_search_findings_start_detached(person: dict) -> None:
    investigation_id = uuid.UUID(person["investigation"]["id"])
    _quick_search(investigation_id)

    with SessionLocal() as db:
        detached = db.query(Finding).filter(Finding.person_id.is_(None)).count()
    assert detached >= 2, "a person-less search leaves its findings unattached"


def test_import_attaches_findings_and_rebuilds_profiles(
    client: TestClient, analyst: dict, person: dict
) -> None:
    investigation_id = uuid.UUID(person["investigation"]["id"])
    search_id = _quick_search(investigation_id)
    person_id = uuid.UUID(person["id"])

    response = client.post(
        f"{API}/persons/{person_id}/import-search",
        json={"search_id": str(search_id)},
        headers=analyst,
    )
    assert response.status_code == 200, response.text
    stats = response.json()

    assert stats["findings_moved"] == 2
    assert stats["runs_moved"] == 1
    # The profile could not exist before: there was no person to attach it to.
    assert stats["profiles_created"] == 1

    with SessionLocal() as db:
        moved = db.query(Finding).filter(Finding.person_id == person_id).count()
        profiles = db.query(SocialProfile).filter(SocialProfile.person_id == person_id).all()
        search = db.get(Search, search_id)
        run = db.query(PluginRun).filter(PluginRun.search_id == search_id).one()

    assert moved == 2
    assert search.person_id == person_id
    assert run.person_id == person_id
    assert [p.username for p in profiles] == ["jdupont"]

    # The correlation ran: the display name matches the person, so the profile
    # is scored rather than left at its raw plugin confidence.
    detail = client.get(f"{API}/persons/{person_id}", headers=analyst).json()
    assert detail["counters"]["findings"] == 2
    assert detail["counters"]["social_profiles"] == 1


def test_importing_twice_is_refused(
    client: TestClient, analyst: dict, person: dict
) -> None:
    investigation_id = uuid.UUID(person["investigation"]["id"])
    search_id = _quick_search(investigation_id)

    first = client.post(
        f"{API}/persons/{person['id']}/import-search",
        json={"search_id": str(search_id)},
        headers=analyst,
    )
    assert first.status_code == 200

    second = client.post(
        f"{API}/persons/{person['id']}/import-search",
        json={"search_id": str(search_id)},
        headers=analyst,
    )
    assert second.status_code == 400
    assert "already attached" in second.json()["detail"]


def test_unknown_search_is_reported(client: TestClient, analyst: dict, person: dict) -> None:
    response = client.post(
        f"{API}/persons/{person['id']}/import-search",
        json={"search_id": str(uuid.uuid4())},
        headers=analyst,
    )
    assert response.status_code == 404


def test_unattached_filter_lists_quick_searches(
    client: TestClient, analyst: dict, person: dict
) -> None:
    investigation_id = uuid.UUID(person["investigation"]["id"])
    search_id = _quick_search(investigation_id)

    listed = client.get(f"{API}/searches?unattached=true", headers=analyst).json()
    assert str(search_id) in [s["id"] for s in listed]
    assert all(s["person_id"] is None for s in listed)

    client.post(
        f"{API}/persons/{person['id']}/import-search",
        json={"search_id": str(search_id)},
        headers=analyst,
    )
    after = client.get(f"{API}/searches?unattached=true", headers=analyst).json()
    assert str(search_id) not in [s["id"] for s in after], "an imported search is no longer orphaned"


def test_reader_cannot_import(client: TestClient, reader: dict, person: dict) -> None:
    investigation_id = uuid.UUID(person["investigation"]["id"])
    search_id = _quick_search(investigation_id)

    response = client.post(
        f"{API}/persons/{person['id']}/import-search",
        json={"search_id": str(search_id)},
        headers=reader,
    )
    assert response.status_code == 403


def test_person_lookup_helper(person: dict) -> None:
    with SessionLocal() as db:
        assert db.get(Person, uuid.UUID(person["id"])) is not None
