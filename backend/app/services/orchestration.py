"""Search planning.

Two guardrails shape the orchestration:
  * DIFFERENTIAL search: a plugin is not re-run against a target it already
    processed successfully and recently;
  * CONTROLLED depth: each level must be explicitly allowed by the requested
    depth, and automation can be switched off per case file.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.enums import IdentifierType, RunStatus
from app.models.identity import Platform, SocialProfile
from app.models.investigation import Person
from app.models.ops import PluginRun
from app.plugins import registry
from app.services.normalization import extract_username_from_url, normalize

#: How long a successful run makes an identical re-run unnecessary.
FRESHNESS_DAYS = 7

#: Plugins that must only be triggered in a specific context.
CONTEXTUAL_PLUGINS = {"toutatis"}


def plan_search(
    db: Session,
    *,
    target_type: str,
    target_value: str,
    depth: int = 1,
    differential: bool = True,
    person: Person | None = None,
    options: dict | None = None,
) -> list[dict]:
    """Build the list of runs to launch at level 1."""
    options = options or {}
    normalized = normalize(target_type, target_value)
    requested = set(options.get("plugins") or [])
    force = bool(options.get("force"))

    steps: list[dict] = []
    for plugin in registry.enabled_plugins(db):
        if not plugin.supports(target_type):
            continue
        if requested and plugin.name not in requested:
            continue
        if plugin.name in CONTEXTUAL_PLUGINS and not _contextual_ok(
            db, plugin.name, target_type, normalized, person, options
        ):
            continue
        if differential and not force and _recently_done(db, plugin.name, target_type, normalized):
            continue

        steps.append(
            {
                "plugin": plugin.name,
                "type": target_type,
                "value": target_value,
                "normalized": normalized,
                "context": _context_for(plugin.name, options),
            }
        )
    return steps


def next_level_tasks(db: Session, new_targets: list[dict], *, depth: int) -> list[dict]:
    """Turn discovered identifiers into runs for the next level."""
    steps: list[dict] = []
    seen: set[tuple[str, str, str]] = set()

    for target in new_targets:
        target_type = target["type"]
        value = target["value"]

        # A profile URL becomes an actionable username search.
        if target_type == IdentifierType.SOCIAL_PROFILE.value:
            username = extract_username_from_url(value)
            if not username:
                continue
            target_type, value = IdentifierType.USERNAME.value, username

        if target_type not in {
            IdentifierType.USERNAME.value,
            IdentifierType.EMAIL.value,
            IdentifierType.PHONE.value,
            IdentifierType.DOMAIN.value,
        }:
            continue

        normalized = normalize(target_type, value)
        for plugin in registry.enabled_plugins(db):
            if not plugin.supports(target_type):
                continue
            if plugin.name in CONTEXTUAL_PLUGINS:
                continue  # never triggered automatically at depth
            key = (plugin.name, target_type, normalized)
            if key in seen or _recently_done(db, plugin.name, target_type, normalized):
                continue
            seen.add(key)
            steps.append(
                {
                    "plugin": plugin.name,
                    "type": target_type,
                    "value": value,
                    "normalized": normalized,
                    "context": {"depth": depth},
                }
            )
    return steps


def compatible_plugins(db: Session, identifier_type: str) -> list[dict]:
    """Enabled plugins applicable to an identifier type (shown in the UI)."""
    out = []
    for plugin in registry.enabled_plugins(db):
        if plugin.supports(identifier_type):
            out.append(
                {
                    "name": plugin.name,
                    "description": plugin.description,
                    "contextual": plugin.name in CONTEXTUAL_PLUGINS,
                }
            )
    return out


def _recently_done(db: Session, plugin: str, target_type: str, normalized: str) -> bool:
    limit = datetime.now(UTC) - timedelta(days=FRESHNESS_DAYS)
    run = db.execute(
        select(PluginRun)
        .where(
            PluginRun.plugin == plugin,
            PluginRun.target_type == target_type,
            PluginRun.normalized_target == normalized,
            PluginRun.status == RunStatus.SUCCESS.value,
            PluginRun.finished_at.is_not(None),
            PluginRun.finished_at > limit,
        )
        .limit(1)
    ).scalars().first()
    return run is not None


def _contextual_ok(
    db: Session,
    plugin_name: str,
    target_type: str,
    normalized: str,
    person: Person | None,
    options: dict,
) -> bool:
    """Toutatis only applies to a username actually linked to Instagram."""
    if plugin_name != "toutatis":
        return True
    if options.get("instagram") is True:
        return True
    if person is None:
        return False
    instagram = db.execute(
        select(Platform).where(Platform.slug == "instagram")
    ).scalar_one_or_none()
    if instagram is None:
        return False
    profile = db.execute(
        select(SocialProfile).where(
            SocialProfile.person_id == person.id,
            SocialProfile.platform_id == instagram.id,
        )
    ).scalars().first()
    if profile is None:
        return False
    return normalize(IdentifierType.USERNAME.value, profile.username) == normalized


def _context_for(plugin_name: str, options: dict) -> dict:
    context: dict = {"depth": 1}
    if plugin_name == "sherlock":
        if options.get("sites"):
            context["sites"] = options["sites"]
        if options.get("site_timeout"):
            context["site_timeout"] = options["site_timeout"]
    if plugin_name == "phoneinfoga" and options.get("region"):
        context["region"] = options["region"]
    if plugin_name == "websearch" and options.get("max_queries"):
        context["max_queries"] = options["max_queries"]
    return context
