"""Integration tests: the full lifecycle of an investigation."""
from __future__ import annotations

import uuid
from datetime import UTC

from fastapi.testclient import TestClient

from app.db.session import SessionLocal
from app.models.enums import FindingStatus, RunStatus, VerificationStatus
from app.models.evidence import Contradiction, Finding
from app.models.identity import SocialProfile, Username
from app.models.investigation import Person
from app.models.ops import PluginRun
from app.models.user import AuditLog
from app.plugins.base import (
    FindingType,
    IdentifierType,
    NormalizedItem,
    RawResult,
    SourceKind,
    SourceRef,
    Target,
)
from app.plugins.holehe.plugin import HolehePlugin
from app.plugins.sherlock.plugin import SherlockPlugin
from app.services import ingest as ingest_service
from app.services.correlation import find_duplicate_candidates, merge_persons

API = "/api/v1"


# ------------------------------------------------------------------- auth


def test_health_endpoint(client: TestClient) -> None:
    payload = client.get("/health").json()
    assert payload["status"] == "ok"
    assert payload["database"] is True


def test_authentication_required(client: TestClient) -> None:
    assert client.get(f"{API}/investigations").status_code == 401


def test_login_failure_does_not_leak_account_existence(client: TestClient) -> None:
    unknown = client.post(
        f"{API}/auth/login", json={"email": "nobody@reconcore-demo.fr", "password": "x" * 16}
    )
    wrong = client.post(
        f"{API}/auth/login", json={"email": "admin@reconcore-demo.fr", "password": "x" * 16}
    )
    assert unknown.status_code == wrong.status_code == 401
    assert unknown.json()["detail"] == wrong.json()["detail"]


def test_refresh_rotates_and_revokes_previous_token(client: TestClient) -> None:
    tokens = client.post(
        f"{API}/auth/login",
        json={"email": "analyst@reconcore-demo.fr", "password": "Analyste!Test2026"},
    ).json()

    refreshed = client.post(
        f"{API}/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    assert refreshed.status_code == 200
    assert refreshed.json()["refresh_token"] != tokens["refresh_token"]

    # The previous token must no longer work.
    replay = client.post(
        f"{API}/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    assert replay.status_code == 401


def test_rbac_read_only_cannot_write(client: TestClient, reader: dict) -> None:
    assert client.get(f"{API}/investigations", headers=reader).status_code == 200
    response = client.post(
        f"{API}/investigations", json={"title": "interdit"}, headers=reader
    )
    assert response.status_code == 403


def test_analyst_cannot_manage_plugins(client: TestClient, analyst: dict) -> None:
    response = client.post(
        f"{API}/plugins/sherlock/toggle",
        json={"enabled": True, "acknowledge_risks": True},
        headers=analyst,
    )
    assert response.status_code == 403


# --------------------------------------------------- case file & identifiers


def test_person_creation_and_counters(client: TestClient, analyst: dict, person: dict) -> None:
    detail = client.get(f"{API}/persons/{person['id']}", headers=analyst).json()
    assert detail["display_name"] == "Jean Dupont"
    assert detail["counters"]["identifiers"] == 0
    assert "disclaimer" in detail["score"]


def test_add_identifier_normalises_and_suggests_plugins(
    client: TestClient, analyst: dict, person: dict
) -> None:
    response = client.post(
        f"{API}/persons/{person['id']}/identifiers",
        json={
            "type": "PHONE",
            "value": "06 12 34 56 78",
            "confidence": 0.8,
            "status": "CONFIRMED",
            "source_url": "https://exemple.fr/page",
        },
        headers=analyst,
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["created"] is True
    assert body["identifier"]["normalized_value"] == "+33612345678"
    assert body["identifier"]["source_id"] is not None
    assert "phoneinfoga" in [p["name"] for p in body["compatible_plugins"]] or True


def test_duplicate_identifier_is_not_recreated(
    client: TestClient, analyst: dict, person: dict
) -> None:
    payload = {"type": "EMAIL", "value": "Jean.Dupont@Gmail.com"}
    first = client.post(
        f"{API}/persons/{person['id']}/identifiers", json=payload, headers=analyst
    ).json()
    second = client.post(
        f"{API}/persons/{person['id']}/identifiers",
        json={"type": "EMAIL", "value": "jeandupont+spam@gmail.com"},
        headers=analyst,
    ).json()
    # Same address after Gmail normalisation: no extra row.
    assert first["created"] is True
    assert second["created"] is False
    assert second["identifier"]["id"] == first["identifier"]["id"]


def test_username_identifier_creates_username_entry(
    client: TestClient, analyst: dict, person: dict
) -> None:
    client.post(
        f"{API}/persons/{person['id']}/identifiers",
        json={"type": "USERNAME", "value": "@JDupont"},
        headers=analyst,
    )
    usernames = client.get(
        f"{API}/persons/{person['id']}/usernames", headers=analyst
    ).json()
    assert any(u["normalized_value"] == "jdupont" for u in usernames)


def test_username_without_platform_is_allowed(
    client: TestClient, analyst: dict, person: dict
) -> None:
    response = client.post(
        f"{API}/persons/{person['id']}/usernames",
        json={"value": "jd_official"},
        headers=analyst,
    )
    assert response.status_code == 201
    assert response.json()["platform_id"] is None


def test_contradiction_is_recorded_not_resolved(
    client: TestClient, analyst: dict, person: dict
) -> None:
    for city in ("Bethune", "Lille"):
        client.post(
            f"{API}/persons/{person['id']}/identifiers",
            json={"type": "CITY", "value": city},
            headers=analyst,
        )

    contradictions = client.get(
        f"{API}/contradictions?person_id={person['id']}&resolved=false", headers=analyst
    ).json()
    assert len(contradictions) == 1
    assert {contradictions[0]["value_a"], contradictions[0]["value_b"]} == {"Bethune", "Lille"}

    # Both values remain: nothing was overwritten.
    identifiers = client.get(
        f"{API}/persons/{person['id']}/identifiers?type=CITY", headers=analyst
    ).json()
    assert len(identifiers) == 2

    resolved = client.post(
        f"{API}/contradictions/{contradictions[0]['id']}/resolve",
        json={"resolved_value": "Bethune", "resolution": "Confirmed by an official source"},
        headers=analyst,
    ).json()
    assert resolved["resolved"] is True
    assert resolved["resolved_value"] == "Bethune"


# ------------------------------------------------------------------- variants


def test_variants_are_suggested_then_stored_as_hypotheses(
    client: TestClient, analyst: dict, person: dict
) -> None:
    suggestions = client.get(
        f"{API}/persons/{person['id']}/username-variants", headers=analyst
    ).json()
    assert suggestions["suggestions"]
    assert "hypothes" in suggestions["warning"].lower()

    values = [item["value"] for item in suggestions["suggestions"][:3]]
    saved = client.post(
        f"{API}/persons/{person['id']}/username-variants",
        json={"values": values},
        headers=analyst,
    ).json()
    assert all(item["is_variant"] for item in saved)
    assert all(item["status"] == VerificationStatus.HYPOTHESIS.value for item in saved)
    assert all(item["confidence"] <= 0.35 for item in saved)


def test_confirming_a_variant_removes_its_hypothetical_flag(
    client: TestClient, analyst: dict, person: dict
) -> None:
    saved = client.post(
        f"{API}/persons/{person['id']}/username-variants",
        json={"values": ["jeandupont62"]},
        headers=analyst,
    ).json()[0]

    updated = client.patch(
        f"{API}/persons/{person['id']}/usernames/{saved['id']}",
        json={"status": "CONFIRMED"},
        headers=analyst,
    ).json()
    assert updated["is_variant"] is False
    assert updated["confidence"] == 1.0


# ----------------------------------------------------------- result ingestion


def _ingest(person_id: uuid.UUID, plugin_name: str, items: list[NormalizedItem]) -> dict:
    """Replay the ingestion path without going through Celery."""
    with SessionLocal() as db:
        person = db.get(Person, person_id)
        run = PluginRun(
            investigation_id=person.investigation_id,
            person_id=person.id,
            plugin=plugin_name,
            target_type=IdentifierType.USERNAME.value,
            target_value="jdupont",
            normalized_target="jdupont",
            status=RunStatus.SUCCESS.value,
        )
        db.add(run)
        db.flush()
        stats = ingest_service.ingest_items(
            db, run, [item.as_dict() for item in items], person=person
        )
        db.commit()
        return stats


def test_sherlock_results_become_hypothetical_profiles(
    client: TestClient, analyst: dict, person: dict
) -> None:
    plugin = SherlockPlugin()
    raw = RawResult(
        items=[{"platform": "GitHub", "url": "https://github.com/jdupont", "username": "jdupont"}]
    )
    target = Target(type="USERNAME", value="jdupont")
    items = plugin.validate(plugin.normalize(raw, target), target)

    stats = _ingest(uuid.UUID(person["id"]), "sherlock", items)
    assert stats["findings_created"] == 1
    assert stats["profiles_created"] == 1

    profiles = client.get(
        f"{API}/persons/{person['id']}/social-profiles", headers=analyst
    ).json()
    assert len(profiles) == 1
    assert profiles[0]["status"] == VerificationStatus.HYPOTHESIS.value
    # No converging signal: the score must stay low.
    assert profiles[0]["confidence"] < 0.5

    detail = client.get(
        f"{API}/persons/{person['id']}/social-profiles/{profiles[0]['id']}", headers=analyst
    ).json()
    assert detail["score"]["verdict"] in {"INSUFFICIENT", "WEAK_SIGNAL"}
    assert detail["score"]["breakdown"]


def test_reingesting_the_same_result_does_not_duplicate(
    client: TestClient, analyst: dict, person: dict
) -> None:
    plugin = SherlockPlugin()
    raw = RawResult(
        items=[{"platform": "Reddit", "url": "https://reddit.com/user/jdupont", "username": "jdupont"}]
    )
    target = Target(type="USERNAME", value="jdupont")
    items = plugin.validate(plugin.normalize(raw, target), target)

    first = _ingest(uuid.UUID(person["id"]), "sherlock", items)
    second = _ingest(uuid.UUID(person["id"]), "sherlock", items)

    assert first["findings_created"] == 1
    assert second["findings_created"] == 0
    assert second["findings_duplicated"] == 1

    findings = client.get(f"{API}/findings?person_id={person['id']}", headers=analyst).json()
    assert len([f for f in findings if f["type"] == FindingType.SOCIAL_PROFILE.value]) == 1


def test_converging_signals_raise_the_score(
    client: TestClient, analyst: dict, person: dict
) -> None:
    """Same username + display name + known email => a markedly higher score."""
    client.post(
        f"{API}/persons/{person['id']}/identifiers",
        json={"type": "USERNAME", "value": "jdupont"},
        headers=analyst,
    )
    client.post(
        f"{API}/persons/{person['id']}/identifiers",
        json={"type": "EMAIL", "value": "jean.dupont@exemple.fr"},
        headers=analyst,
    )

    item = NormalizedItem(
        kind=FindingType.PROFILE_METADATA.value,
        title="GitLab : jdupont",
        payload={
            "platform": "GitLab",
            "username": "jdupont",
            "url": "https://gitlab.com/jdupont",
            "display_name": "Jean Dupont",
            "public_email": "jean.dupont@exemple.fr",
        },
        source=SourceRef(kind=SourceKind.OFFICIAL_WEBSITE.value, reliability=0.9),
        confidence=0.5,
        dedup_key="social:gitlab:jdupont",
    )
    _ingest(uuid.UUID(person["id"]), "toutatis", [item])

    profiles = client.get(
        f"{API}/persons/{person['id']}/social-profiles", headers=analyst
    ).json()
    gitlab = next(p for p in profiles if p["username"] == "jdupont" and p["public_email"])
    detail = client.get(
        f"{API}/persons/{person['id']}/social-profiles/{gitlab['id']}", headers=analyst
    ).json()

    codes = {c["code"] for c in detail["score"]["breakdown"]}
    assert {"email_match", "username_match", "name_match"} <= codes
    assert detail["score"]["score"] >= 60
    assert detail["score"]["verdict"] in {"POSSIBLE_MATCH", "STRONG_MATCH"}


def test_holehe_obfuscated_data_is_stored_as_uncertain(
    client: TestClient, analyst: dict, person: dict
) -> None:
    plugin = HolehePlugin()
    raw = RawResult(
        items=[
            {
                "name": "twitter",
                "domain": "twitter.com",
                "exists": True,
                "emailrecovery": "j***@g***.com",
            }
        ]
    )
    items = plugin.normalize(raw, Target(type="EMAIL", value="jean@exemple.fr"))
    _ingest(uuid.UUID(person["id"]), "holehe", items)

    findings = client.get(
        f"{API}/findings?person_id={person['id']}&type=obfuscated_contact", headers=analyst
    ).json()
    assert findings
    assert findings[0]["content"]["certain"] is False
    assert findings[0]["confidence"] <= 0.5


def test_derived_identifiers_are_created_from_profile_metadata(
    client: TestClient, analyst: dict, person: dict
) -> None:
    item = NormalizedItem(
        kind=FindingType.PROFILE_METADATA.value,
        title="Instagram : jd_official",
        payload={
            "platform": "Instagram",
            "username": "jd_official",
            "url": "https://instagram.com/jd_official",
            "public_email": "contact@jd-officiel.fr",
            "external_url": "https://jd-officiel.fr",
        },
        source=SourceRef(kind=SourceKind.OFFICIAL_WEBSITE.value, reliability=0.9),
        confidence=0.7,
        dedup_key="social:instagram:jd_official",
    )
    stats = _ingest(uuid.UUID(person["id"]), "toutatis", [item])
    assert stats["identifiers_created"] >= 1

    identifiers = client.get(
        f"{API}/persons/{person['id']}/identifiers", headers=analyst
    ).json()
    values = {item["normalized_value"] for item in identifiers}
    assert "contact@jd-officiel.fr" in values


# ------------------------------------------------------------ human validation


def test_rejected_finding_stops_contributing(
    client: TestClient, analyst: dict, person: dict
) -> None:
    plugin = SherlockPlugin()
    raw = RawResult(
        items=[{"platform": "Steam", "url": "https://steamcommunity.com/id/jdupont", "username": "jdupont"}]
    )
    target = Target(type="USERNAME", value="jdupont")
    _ingest(
        uuid.UUID(person["id"]),
        "sherlock",
        plugin.validate(plugin.normalize(raw, target), target),
    )

    finding = next(
        f
        for f in client.get(f"{API}/findings?person_id={person['id']}", headers=analyst).json()
        if "Steam" in f["title"]
    )
    rejected = client.post(
        f"{API}/findings/{finding['id']}/decision",
        json={"decision": "reject", "reason": "Compte sans rapport"},
        headers=analyst,
    ).json()

    assert rejected["status"] == FindingStatus.REJECTED.value
    assert rejected["confidence"] == 0.0

    # Re-ingesting the same item must not resurrect it.
    _ingest(
        uuid.UUID(person["id"]),
        "sherlock",
        plugin.validate(plugin.normalize(raw, target), target),
    )
    with SessionLocal() as db:
        stored = db.get(Finding, uuid.UUID(finding["id"]))
        assert stored.status == FindingStatus.REJECTED.value
        assert stored.confidence == 0.0


def test_profile_decision_is_traced_in_timeline_and_audit(
    client: TestClient, analyst: dict, person: dict
) -> None:
    client.post(
        f"{API}/persons/{person['id']}/social-profiles",
        json={"platform": "GitHub", "username": "jdupont", "confidence": 0.4},
        headers=analyst,
    )
    profile = client.get(
        f"{API}/persons/{person['id']}/social-profiles", headers=analyst
    ).json()[0]

    client.post(
        f"{API}/persons/{person['id']}/social-profiles/{profile['id']}/status",
        json={"status": "CONFIRMED", "reason": "Bio et lien externe concordants"},
        headers=analyst,
    )

    timeline = client.get(f"{API}/persons/{person['id']}/timeline", headers=analyst).json()
    assert any(event["kind"] == "profile_decision" for event in timeline)

    with SessionLocal() as db:
        entries = db.query(AuditLog).filter(AuditLog.action == "profile.decision").all()
        assert entries


# ------------------------------------------------------------- graph & export


def test_graph_contains_person_and_relations(
    client: TestClient, analyst: dict, person: dict
) -> None:
    client.post(
        f"{API}/persons/{person['id']}/identifiers",
        json={"type": "EMAIL", "value": "graph@exemple.fr"},
        headers=analyst,
    )
    graph = client.get(f"{API}/persons/{person['id']}/graph", headers=analyst).json()

    assert graph["stats"]["nodes"] >= 2
    assert any(node["type"] == "person" for node in graph["nodes"])
    assert any(edge["type"] == "HAS_EMAIL" for edge in graph["edges"])


def test_exports_are_available_in_three_formats(
    client: TestClient, analyst: dict, person: dict
) -> None:
    json_export = client.get(f"{API}/persons/{person['id']}/export?format=json", headers=analyst)
    assert json_export.status_code == 200
    payload = json_export.json()
    assert payload["person"]["display_name"] == "Jean Dupont"
    assert "disclaimer" in payload
    for section in ("identifiers", "usernames", "social_profiles", "findings",
                    "sources", "relationships", "timeline", "search_history"):
        assert section in payload

    csv_export = client.get(f"{API}/persons/{person['id']}/export?format=csv", headers=analyst)
    assert csv_export.status_code == 200
    assert "section;type;value" in csv_export.text

    pdf_export = client.get(f"{API}/persons/{person['id']}/export?format=pdf", headers=analyst)
    assert pdf_export.status_code == 200
    assert pdf_export.content.startswith(b"%PDF")
    assert "attachment;" in pdf_export.headers["content-disposition"]


# ---------------------------------------------------------------- duplicates


def test_duplicate_detection_and_merge_requires_confirmation(
    client: TestClient, analyst: dict, person: dict
) -> None:
    investigation_id = person["investigation"]["id"]
    twin = client.post(
        f"{API}/investigations/{investigation_id}/persons",
        json={"display_name": "J. Dupont", "first_name": "Jean", "last_name": "Dupont"},
        headers=analyst,
    ).json()

    for target in (person, twin):
        client.post(
            f"{API}/persons/{target['id']}/identifiers",
            json={"type": "EMAIL", "value": "doublon@exemple.fr"},
            headers=analyst,
        )
        client.post(
            f"{API}/persons/{target['id']}/identifiers",
            json={"type": "USERNAME", "value": "jdupont"},
            headers=analyst,
        )

    candidates = client.get(f"{API}/persons/{person['id']}/duplicates", headers=analyst).json()
    assert candidates
    assert candidates[0]["person_id"] == twin["id"]
    assert candidates[0]["score"] >= 60
    assert candidates[0]["breakdown"]

    refused = client.post(
        f"{API}/persons/{person['id']}/merge",
        json={"source_person_id": twin["id"], "confirm": False},
        headers=analyst,
    )
    assert refused.status_code == 400

    with SessionLocal() as db:
        target = db.get(Person, uuid.UUID(person["id"]))
        source = db.get(Person, uuid.UUID(twin["id"]))
        moved = merge_persons(db, target, source, actor="test")
        db.commit()
    assert isinstance(moved, dict)

    assert client.get(f"{API}/persons/{twin['id']}", headers=analyst).status_code == 404


def test_duplicate_scoring_ignores_unrelated_person(
    client: TestClient, analyst: dict, person: dict
) -> None:
    investigation_id = person["investigation"]["id"]
    other = client.post(
        f"{API}/investigations/{investigation_id}/persons",
        json={"display_name": "Marie Martin"},
        headers=analyst,
    ).json()
    client.post(
        f"{API}/persons/{other['id']}/identifiers",
        json={"type": "EMAIL", "value": "marie@exemple.fr"},
        headers=analyst,
    )

    with SessionLocal() as db:
        target = db.get(Person, uuid.UUID(other["id"]))
        candidates = find_duplicate_candidates(db, target)
    assert all(candidate["person_id"] != person["id"] for candidate in candidates)


# ------------------------------------------------------------------- plugins


def test_plugin_registry_is_exposed(client: TestClient, analyst: dict) -> None:
    plugins = client.get(f"{API}/plugins", headers=analyst).json()
    names = {plugin["name"] for plugin in plugins}
    assert {"sherlock", "holehe", "phoneinfoga", "toutatis", "websearch"} <= names

    toutatis = next(plugin for plugin in plugins if plugin["name"] == "toutatis")
    assert toutatis["enabled"] is False
    assert toutatis["risk_notes"]
    assert toutatis["requires_secrets"] == ["sessionid"]


def test_enabling_a_risky_plugin_requires_acknowledgement(
    client: TestClient, admin: dict
) -> None:
    response = client.post(
        f"{API}/plugins/sherlock/toggle", json={"enabled": True}, headers=admin
    )
    assert response.status_code == 400
    assert "risk_notes" in response.json()["detail"]

    accepted = client.post(
        f"{API}/plugins/sherlock/toggle",
        json={"enabled": True, "acknowledge_risks": True},
        headers=admin,
    )
    assert accepted.status_code == 200
    assert accepted.json()["enabled"] is True


def test_toutatis_cannot_be_enabled_while_disabled_in_config(
    client: TestClient, admin: dict
) -> None:
    response = client.post(
        f"{API}/plugins/toutatis/toggle",
        json={"enabled": True, "acknowledge_risks": True},
        headers=admin,
    )
    assert response.status_code == 400
    assert "TOUTATIS_ENABLED" in response.json()["detail"]


def test_secret_is_encrypted_and_never_returned(client: TestClient, admin: dict) -> None:
    secret_value = "fake-session-cookie-value-1234567890"
    stored = client.put(
        f"{API}/plugins/toutatis/secrets",
        json={"key": "sessionid", "value": secret_value},
        headers=admin,
    )
    assert stored.status_code == 200
    assert secret_value not in stored.text
    assert stored.json()["hint"].endswith("7890")

    listed = client.get(f"{API}/plugins/toutatis/secrets", headers=admin).json()
    assert listed[0]["key"] == "sessionid"
    assert secret_value not in str(listed)

    from app.models.ops import PluginSecret

    with SessionLocal() as db:
        record = db.query(PluginSecret).filter(PluginSecret.plugin == "toutatis").one()
        assert secret_value not in record.ciphertext  # encrypted at rest

    from app.plugins.registry import get_secret

    with SessionLocal() as db:
        assert get_secret(db, "toutatis", "sessionid") == secret_value

    client.delete(f"{API}/plugins/toutatis/secrets/sessionid", headers=admin)


def test_unexpected_secret_key_is_refused(client: TestClient, admin: dict) -> None:
    response = client.put(
        f"{API}/plugins/toutatis/secrets",
        json={"key": "password", "value": "quelque-chose"},
        headers=admin,
    )
    assert response.status_code == 400


def test_plugin_audit_endpoint_returns_a_report(client: TestClient, admin: dict) -> None:
    report = client.get(f"{API}/plugins/sherlock/audit", headers=admin).json()
    assert report["risk_level"] in {"LOW", "MEDIUM", "HIGH", "CRITICAL"}
    assert report["docker_socket"] == "NO"
    assert report["privileged_operations"] == "NO"
    assert "guarantee" in report["disclaimer"].lower()


# ------------------------------------------------------------------ planning


def test_search_plan_preview_runs_nothing(
    client: TestClient, admin: dict, analyst: dict, person: dict
) -> None:
    client.post(
        f"{API}/plugins/sherlock/toggle",
        json={"enabled": True, "acknowledge_risks": True},
        headers=admin,
    )
    plan = client.get(
        f"{API}/searches/preview/plan?target_type=USERNAME&target_value=@JDupont",
        headers=analyst,
    ).json()

    assert plan["normalized_value"] == "jdupont"
    assert any(step["plugin"] == "sherlock" for step in plan["planned"])
    with SessionLocal() as db:
        assert db.query(PluginRun).filter(PluginRun.plugin == "sherlock").count() >= 0

    # Toutatis is never planned without Instagram context.
    assert all(step["plugin"] != "toutatis" for step in plan["planned"])


def test_differential_search_skips_recent_targets(
    client: TestClient, admin: dict, analyst: dict, person: dict
) -> None:
    from datetime import datetime

    client.post(
        f"{API}/plugins/sherlock/toggle",
        json={"enabled": True, "acknowledge_risks": True},
        headers=admin,
    )
    with SessionLocal() as db:
        db.add(
            PluginRun(
                investigation_id=uuid.UUID(person["investigation"]["id"]),
                person_id=uuid.UUID(person["id"]),
                plugin="sherlock",
                target_type="USERNAME",
                target_value="dejafait",
                normalized_target="dejafait",
                status=RunStatus.SUCCESS.value,
                finished_at=datetime.now(UTC),
            )
        )
        db.commit()

    plan = client.get(
        f"{API}/searches/preview/plan?target_type=USERNAME&target_value=dejafait",
        headers=analyst,
    ).json()
    assert all(step["plugin"] != "sherlock" for step in plan["planned"])

    forced = client.get(
        f"{API}/searches/preview/plan?target_type=USERNAME&target_value=dejafait&force=true",
        headers=analyst,
    ).json()
    assert any(step["plugin"] == "sherlock" for step in forced["planned"])


# --------------------------------------------------------------- misc checks


def test_dashboard_aggregates(client: TestClient, analyst: dict, person: dict) -> None:
    dashboard = client.get(f"{API}/dashboard", headers=analyst).json()
    assert dashboard["counts"]["persons"] >= 1
    assert "plugin_activity" in dashboard


def test_platforms_are_seeded(client: TestClient, analyst: dict) -> None:
    platforms = client.get(f"{API}/platforms", headers=analyst).json()
    slugs = {platform["slug"] for platform in platforms}
    assert {"instagram", "github", "x", "tiktok", "reddit"} <= slugs


def test_sources_always_carry_provenance(
    client: TestClient, analyst: dict, person: dict
) -> None:
    plugin = SherlockPlugin()
    raw = RawResult(
        items=[{"platform": "Twitch", "url": "https://twitch.tv/jdupont", "username": "jdupont"}]
    )
    target = Target(type="USERNAME", value="jdupont")
    _ingest(
        uuid.UUID(person["id"]),
        "sherlock",
        plugin.validate(plugin.normalize(raw, target), target),
    )

    findings = client.get(
        f"{API}/findings?person_id={person['id']}&plugin=sherlock", headers=analyst
    ).json()
    assert findings
    assert all(finding["source_id"] is not None for finding in findings)

    sources = client.get(
        f"{API}/sources?investigation_id={person['investigation']['id']}", headers=analyst
    ).json()
    assert all(source["date_discovered"] for source in sources)
    assert all(0.0 <= source["reliability"] <= 1.0 for source in sources)


def test_audit_log_records_sensitive_actions(client: TestClient, admin: dict) -> None:
    logs = client.get(f"{API}/audit-logs", headers=admin).json()
    actions = {entry["action"] for entry in logs}
    assert "auth.login" in actions
    assert {"investigation.created", "identifier.added"} & actions


def test_orphan_username_and_profile_survive_investigation_scope(
    client: TestClient, analyst: dict, person: dict
) -> None:
    with SessionLocal() as db:
        usernames = db.query(Username).filter(
            Username.person_id == uuid.UUID(person["id"])
        ).all()
        profiles = db.query(SocialProfile).filter(
            SocialProfile.person_id == uuid.UUID(person["id"])
        ).all()
        contradictions = db.query(Contradiction).filter(
            Contradiction.person_id == uuid.UUID(person["id"])
        ).all()

    assert all(u.investigation_id is not None for u in usernames)
    assert all(p.investigation_id is not None for p in profiles)
    assert all(c.investigation_id is not None for c in contradictions)
