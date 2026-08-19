"""Plugin discovery and registry.

A plugin is a sub-package of `app.plugins` exposing a `PLUGIN` variable that
points at an `OSINTPlugin` subclass. Discovery is explicit: no code is ever
imported from an arbitrary path or downloaded at runtime.
"""
from __future__ import annotations

import importlib
import pkgutil
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models.ops import PluginRegistryEntry, PluginSecret
from app.plugins.base import OSINTPlugin
from app.security.crypto import decrypt, encrypt, mask

logger = get_logger(__name__)

PLUGINS_DIR = Path(__file__).parent

_CACHE: dict[str, OSINTPlugin] | None = None


def discover(force: bool = False) -> dict[str, OSINTPlugin]:
    """Load the plugins found in `app/plugins/*/plugin.py`."""
    global _CACHE
    if _CACHE is not None and not force:
        return _CACHE

    found: dict[str, OSINTPlugin] = {}
    for module_info in pkgutil.iter_modules([str(PLUGINS_DIR)]):
        if not module_info.ispkg:
            continue
        module_name = f"app.plugins.{module_info.name}.plugin"
        try:
            module = importlib.import_module(module_name)
        except ModuleNotFoundError:
            continue
        except Exception as exc:  # pragma: no cover - broken plugin
            logger.error("Could not load plugin %s: %s", module_info.name, exc)
            continue
        plugin_cls = getattr(module, "PLUGIN", None)
        if plugin_cls is None or not issubclass(plugin_cls, OSINTPlugin):
            logger.warning("%s does not expose a valid PLUGIN", module_name)
            continue
        instance = plugin_cls()
        found[instance.name] = instance

    _CACHE = found
    logger.info("Plugins discovered: %s", ", ".join(sorted(found)) or "none")
    return found


def get(name: str) -> OSINTPlugin | None:
    return discover().get(name)


def all_plugins() -> list[OSINTPlugin]:
    return sorted(discover().values(), key=lambda p: p.name)


def plugins_for(identifier_type: str) -> list[OSINTPlugin]:
    return [p for p in all_plugins() if p.supports(identifier_type)]


# ------------------------------------------------------------- persistence


def sync_registry(db: Session) -> list[PluginRegistryEntry]:
    """Create or refresh the `plugins` row for every discovered plugin.

    Existing activation is never overwritten: a plugin an administrator disabled
    stays disabled across code updates.
    """
    entries: list[PluginRegistryEntry] = []
    existing = {
        e.name: e for e in db.execute(select(PluginRegistryEntry)).scalars().all()
    }
    for plugin in all_plugins():
        entry = existing.get(plugin.name)
        if entry is None:
            entry = PluginRegistryEntry(
                name=plugin.name, enabled=plugin.enabled_by_default
            )
            db.add(entry)
        entry.version = plugin.version
        entry.description = plugin.description
        entry.repository = plugin.repository
        entry.license = plugin.license
        entry.supported_identifiers = list(plugin.supported_identifiers)
        entry.requires_secrets = list(plugin.requires_secrets)
        if entry.requests_per_minute is None:
            entry.requests_per_minute = plugin.requests_per_minute
        entries.append(entry)
    db.flush()
    return entries


def get_entry(db: Session, name: str) -> PluginRegistryEntry | None:
    return db.execute(
        select(PluginRegistryEntry).where(PluginRegistryEntry.name == name)
    ).scalar_one_or_none()


def is_enabled(db: Session, name: str) -> bool:
    entry = get_entry(db, name)
    return bool(entry and entry.enabled)


def enabled_plugins(db: Session) -> list[OSINTPlugin]:
    enabled = {
        e.name
        for e in db.execute(
            select(PluginRegistryEntry).where(PluginRegistryEntry.enabled.is_(True))
        )
        .scalars()
        .all()
    }
    return [p for p in all_plugins() if p.name in enabled]


def effective_limits(db: Session, plugin: OSINTPlugin) -> dict:
    entry = get_entry(db, plugin.name)
    if entry is None:
        return {
            "requests_per_minute": plugin.requests_per_minute,
            "concurrency": plugin.concurrency,
            "timeout_seconds": plugin.timeout_seconds,
            "retry_count": plugin.retry_count,
        }
    return {
        "requests_per_minute": entry.requests_per_minute,
        "concurrency": entry.concurrency,
        "timeout_seconds": entry.timeout_seconds,
        "retry_count": entry.retry_count,
    }


# ------------------------------------------------------------------ secrets


def set_secret(
    db: Session, plugin: str, key: str, value: str, user_id=None
) -> PluginSecret:
    """Encrypt then store a plugin secret. The clear value is never logged."""
    record = db.execute(
        select(PluginSecret).where(
            PluginSecret.plugin == plugin, PluginSecret.key == key
        )
    ).scalar_one_or_none()
    ciphertext = encrypt(value)
    if record is None:
        record = PluginSecret(plugin=plugin, key=key, ciphertext=ciphertext)
        db.add(record)
    else:
        record.ciphertext = ciphertext
    record.hint = mask(value)
    record.set_by_id = user_id
    db.flush()
    return record


def get_secret(db: Session, plugin: str, key: str) -> str | None:
    record = db.execute(
        select(PluginSecret).where(
            PluginSecret.plugin == plugin, PluginSecret.key == key
        )
    ).scalar_one_or_none()
    return decrypt(record.ciphertext) if record else None


def delete_secret(db: Session, plugin: str, key: str) -> bool:
    record = db.execute(
        select(PluginSecret).where(
            PluginSecret.plugin == plugin, PluginSecret.key == key
        )
    ).scalar_one_or_none()
    if record is None:
        return False
    db.delete(record)
    return True


def secret_status(db: Session, plugin: OSINTPlugin) -> dict[str, bool]:
    keys = set(plugin.requires_secrets)
    if not keys:
        return {}
    present = {
        s.key
        for s in db.execute(
            select(PluginSecret).where(PluginSecret.plugin == plugin.name)
        )
        .scalars()
        .all()
    }
    return {key: key in present for key in keys}


def mark_health(db: Session, name: str, ok: bool, message: str) -> None:
    entry = get_entry(db, name)
    if entry is None:
        return
    entry.health_status = "OK" if ok else "ERROR"
    entry.health_message = message[:2000]
    entry.health_checked_at = datetime.now(UTC)
