"""Plugin registry: state, activation, quotas, secrets, security audit."""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import db_session, get_current_user, require_admin
from app.core.config import settings
from app.models.ops import PluginSecret
from app.models.user import User
from app.plugins import audit as plugin_audit
from app.plugins import registry
from app.schemas.common import Message
from app.schemas.plugins import (
    PluginAuditOut,
    PluginHealthOut,
    PluginLimits,
    PluginLimitsUpdate,
    PluginOut,
    PluginSecretOut,
    PluginSecretSet,
    PluginToggle,
)
from app.security import audit as audit_log
from app.security.crypto import SecretsUnavailable, secrets_available

router = APIRouter(prefix="/plugins", tags=["plugins"])


def _serialize(db: Session, plugin) -> PluginOut:
    entry = registry.get_entry(db, plugin.name)
    limits = registry.effective_limits(db, plugin)
    return PluginOut(
        name=plugin.name,
        version=plugin.version,
        description=plugin.description,
        repository=plugin.repository,
        license=plugin.license,
        enabled=bool(entry and entry.enabled),
        supported_identifiers=plugin.supported_identifiers,
        requires_secrets=plugin.requires_secrets,
        secrets_configured=registry.secret_status(db, plugin),
        risk_level=entry.risk_level if entry else "UNKNOWN",
        risk_notes=plugin.risk_notes,
        last_audit_at=entry.last_audit_at if entry else None,
        health_status=entry.health_status if entry else None,
        health_message=entry.health_message if entry else None,
        health_checked_at=entry.health_checked_at if entry else None,
        limits=PluginLimits(**limits),
        queue=plugin.queue,
    )


@router.get("", response_model=list[PluginOut])
def list_plugins(
    db: Session = Depends(db_session), _: User = Depends(get_current_user)
) -> list[PluginOut]:
    registry.sync_registry(db)
    return [_serialize(db, plugin) for plugin in registry.all_plugins()]


@router.get("/{name}", response_model=PluginOut)
def get_plugin(
    name: str, db: Session = Depends(db_session), _: User = Depends(get_current_user)
) -> PluginOut:
    plugin = registry.get(name)
    if plugin is None:
        raise HTTPException(status_code=404, detail="Unknown plugin")
    registry.sync_registry(db)
    return _serialize(db, plugin)


@router.post("/{name}/toggle", response_model=PluginOut)
def toggle(
    name: str,
    payload: PluginToggle,
    request: Request,
    db: Session = Depends(db_session),
    user: User = Depends(require_admin),
) -> PluginOut:
    """Enable or disable a plugin. Risky plugins require explicit acknowledgement."""
    plugin = registry.get(name)
    if plugin is None:
        raise HTTPException(status_code=404, detail="Unknown plugin")
    registry.sync_registry(db)
    entry = registry.get_entry(db, name)

    if payload.enabled:
        if plugin.risk_notes and not payload.acknowledge_risks:
            raise HTTPException(
                status_code=400,
                detail={
                    "message": (
                        "This plugin carries warnings. Confirm with "
                        "acknowledge_risks=true once you have read them."
                    ),
                    "risk_notes": plugin.risk_notes,
                },
            )
        if name == "toutatis" and not settings.toutatis_enabled:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Toutatis is disabled at configuration level. Set "
                    "TOUTATIS_ENABLED=true in .env and restart."
                ),
            )
        missing = [
            key for key, present in registry.secret_status(db, plugin).items() if not present
        ]
        if missing:
            raise HTTPException(
                status_code=400,
                detail=f"Missing secrets for this plugin: {', '.join(missing)}",
            )

    entry.enabled = payload.enabled
    audit_log.record(
        db,
        action="plugin.toggled",
        user=user,
        object_type="plugin",
        object_id=name,
        message=f"Plugin {name} {'enabled' if payload.enabled else 'disabled'}",
        detail={"acknowledged_risks": payload.acknowledge_risks},
        request=request,
    )
    return _serialize(db, plugin)


@router.patch("/{name}/limits", response_model=PluginOut)
def update_limits(
    name: str,
    payload: PluginLimitsUpdate,
    db: Session = Depends(db_session),
    user: User = Depends(require_admin),
) -> PluginOut:
    plugin = registry.get(name)
    if plugin is None:
        raise HTTPException(status_code=404, detail="Unknown plugin")
    registry.sync_registry(db)
    entry = registry.get_entry(db, name)
    for field, value in payload.model_dump(exclude_none=True).items():
        setattr(entry, field, value)
    return _serialize(db, plugin)


@router.get("/{name}/audit", response_model=PluginAuditOut)
def audit_plugin(
    name: str,
    request: Request,
    db: Session = Depends(db_session),
    user: User = Depends(require_admin),
) -> dict:
    """Static security analysis of the plugin (a decision aid, not a guarantee)."""
    if registry.get(name) is None:
        raise HTTPException(status_code=404, detail="Unknown plugin")

    report = plugin_audit.audit_plugin(name)
    summary = report.summary()

    registry.sync_registry(db)
    entry = registry.get_entry(db, name)
    entry.risk_level = report.risk_level
    entry.last_audit_at = datetime.now(UTC)
    entry.audit_report = summary

    audit_log.record(
        db,
        action="plugin.audited",
        user=user,
        object_type="plugin",
        object_id=name,
        message=f"Security audit of {name}: {report.risk_level}",
        request=request,
    )
    return summary


@router.post("/{name}/health", response_model=PluginHealthOut)
def health(
    name: str, db: Session = Depends(db_session), _: User = Depends(require_admin)
) -> dict:
    """Probe the plugin inside its dedicated worker (result stored in the DB)."""
    plugin = registry.get(name)
    if plugin is None:
        raise HTTPException(status_code=404, detail="Unknown plugin")
    db.commit()

    from app.workers.tasks import plugin_health

    async_result = plugin_health.apply_async(args=[name], queue=plugin.queue)
    try:
        payload = async_result.get(timeout=90)
    except Exception as exc:
        raise HTTPException(
            status_code=504,
            detail=(
                f"Worker '{plugin.queue}' did not answer ({exc}). "
                "Check that the matching container is running."
            ),
        ) from exc
    return payload


# ------------------------------------------------------------------ secrets


@router.get("/{name}/secrets", response_model=list[PluginSecretOut])
def list_secrets(
    name: str, db: Session = Depends(db_session), _: User = Depends(require_admin)
) -> list[PluginSecretOut]:
    """List configured secrets. Values are NEVER returned."""
    records = db.execute(
        select(PluginSecret).where(PluginSecret.plugin == name)
    ).scalars().all()
    return [
        PluginSecretOut(
            plugin=r.plugin, key=r.key, hint=r.hint, updated_at=r.updated_at
        )
        for r in records
    ]


@router.put("/{name}/secrets", response_model=PluginSecretOut)
def set_secret(
    name: str,
    payload: PluginSecretSet,
    request: Request,
    db: Session = Depends(db_session),
    user: User = Depends(require_admin),
) -> PluginSecretOut:
    """Store an encrypted secret (e.g. a session cookie). Never a password."""
    plugin = registry.get(name)
    if plugin is None:
        raise HTTPException(status_code=404, detail="Unknown plugin")
    if payload.key not in plugin.requires_secrets:
        raise HTTPException(
            status_code=400,
            detail=f"This plugin expects no '{payload.key}' secret. "
            f"Expected keys: {plugin.requires_secrets}",
        )
    if any(word in payload.key.lower() for word in ("password", "motdepasse", "passwd")):
        raise HTTPException(
            status_code=400,
            detail="Storing passwords is forbidden by design.",
        )
    if not secrets_available():
        raise HTTPException(
            status_code=503,
            detail=(
                "SECRETS_ENCRYPTION_KEY is not configured: refusing to store a "
                "secret. See .env.example."
            ),
        )

    try:
        record = registry.set_secret(db, name, payload.key, payload.value, user_id=user.id)
    except SecretsUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    audit_log.record(
        db,
        action="plugin.secret_set",
        user=user,
        object_type="plugin",
        object_id=name,
        message=f"Secret '{payload.key}' set for {name} (value not logged)",
        request=request,
    )
    return PluginSecretOut(
        plugin=record.plugin, key=record.key, hint=record.hint, updated_at=record.updated_at
    )


@router.delete("/{name}/secrets/{key}", response_model=Message)
def delete_secret(
    name: str,
    key: str,
    request: Request,
    db: Session = Depends(db_session),
    user: User = Depends(require_admin),
) -> Message:
    if not registry.delete_secret(db, name, key):
        raise HTTPException(status_code=404, detail="Secret not found")
    audit_log.record(
        db,
        action="plugin.secret_deleted",
        user=user,
        object_type="plugin",
        object_id=name,
        message=f"Secret '{key}' deleted for {name}",
        request=request,
    )
    return Message(detail="Secret deleted")
