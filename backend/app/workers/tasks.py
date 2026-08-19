"""Celery tasks: orchestration, plugin execution, persistence.

Strict separation:
  * `plugin_execute` runs inside the tool worker. It only knows the target and
    returns data. No database access.
  * `persist_plugin_result` runs in the default worker, which holds PostgreSQL
    access, applies deduplication and scoring, and decides whether to continue
    deeper.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from celery import chain
from sqlalchemy import select

from app.core.logging import get_logger
from app.db.session import session_scope
from app.models.enums import IdentifierType, RunStatus
from app.models.investigation import Investigation, Person
from app.models.ops import PluginRun, Search
from app.plugins import registry
from app.plugins.base import Target
from app.security.ratelimit import PluginRateLimiter
from app.services import identifiers as ident_service
from app.services import ingest as ingest_service
from app.services.correlation import recompute_person_score
from app.services.orchestration import next_level_tasks, plan_search
from app.workers.celery_app import celery_app

logger = get_logger(__name__)

MAX_DEPTH = 4


def _now() -> datetime:
    return datetime.now(UTC)


# --------------------------------------------------------------- execution


@celery_app.task(name="reconcore.plugin_execute", bind=True)
def plugin_execute(self, plugin_name: str, target_payload: dict, limits: dict) -> dict:
    """Run a plugin inside ITS worker. Returns raw output plus normalised items."""
    plugin = registry.get(plugin_name)
    if plugin is None:
        return {"plugin": plugin_name, "error": f"unknown plugin: {plugin_name}", "items": []}

    target = Target(
        type=target_payload["type"],
        value=target_payload["value"],
        normalized=target_payload.get("normalized", ""),
        context=target_payload.get("context", {}),
    )
    target.context.setdefault("timeout", limits.get("timeout_seconds"))

    # Deliberately self-imposed quota so third-party services are not overloaded.
    PluginRateLimiter(plugin.name, limits.get("requests_per_minute", 30)).acquire()

    try:
        raw, items = plugin.run(target)
    except Exception as exc:  # pragma: no cover - guard against third-party tools
        logger.exception("Plugin %s failed", plugin_name)
        return {
            "plugin": plugin_name,
            "version": plugin.version,
            "error": f"{type(exc).__name__}: {exc}",
            "items": [],
            "raw": {},
        }

    return {
        "plugin": plugin_name,
        "version": plugin.version,
        "error": raw.error,
        "raw": raw.as_dict(),
        "items": [item.as_dict() for item in items],
        "task_id": self.request.id,
    }


@celery_app.task(name="reconcore.persist_plugin_result")
def persist_plugin_result(payload: dict, run_id: str) -> dict:
    """Persist a plugin result and possibly trigger the next level."""
    with session_scope() as db:
        run = db.get(PluginRun, uuid.UUID(run_id))
        if run is None:
            return {"error": "run not found"}

        raw = payload.get("raw") or {}
        run.raw_output = {"meta": raw.get("meta"), "items_count": len(raw.get("items", []))}
        run.logs = raw.get("logs", [])[:500]
        run.duration_ms = raw.get("duration_ms")
        run.finished_at = _now()
        run.progress = 1.0
        run.plugin_version = payload.get("version")

        if payload.get("error"):
            run.status = RunStatus.FAILED.value
            run.error = str(payload["error"])[:2000]
            _finish_search_if_done(db, run)
            return {"run_id": run_id, "status": run.status, "error": run.error}

        person = db.get(Person, run.person_id) if run.person_id else None
        stats = ingest_service.ingest_items(db, run, payload.get("items", []), person=person)
        run.status = RunStatus.SUCCESS.value

        if person is not None:
            recompute_person_score(db, person)
            ident_service.add_timeline(
                db,
                person,
                kind="plugin_run",
                message=(
                    f"{run.plugin} finished on {run.target_type} "
                    f"{run.target_value}: {stats['findings_created']} new result(s)"
                ),
                actor=run.plugin,
                payload={"run_id": str(run.id), **{k: v for k, v in stats.items() if k != "new_targets"}},
            )

        # --- Depth search: discovered entities become the next targets ---
        dispatched = 0
        if run.depth < MAX_DEPTH and stats["new_targets"]:
            search = db.get(Search, run.search_id) if run.search_id else None
            max_depth = min(search.depth if search else 1, MAX_DEPTH)
            investigation = (
                db.get(Investigation, run.investigation_id) if run.investigation_id else None
            )
            automation = investigation.automation_enabled if investigation else True
            if automation and run.depth < max_depth:
                dispatched = _dispatch(
                    db,
                    next_level_tasks(db, stats["new_targets"], depth=run.depth + 1),
                    search=search,
                    person=person,
                    investigation_id=run.investigation_id,
                    depth=run.depth + 1,
                )

        _finish_search_if_done(db, run)
        return {
            "run_id": run_id,
            "status": run.status,
            "stats": {k: v for k, v in stats.items() if k != "new_targets"},
            "dispatched_next_level": dispatched,
        }


# ------------------------------------------------------------ orchestration


@celery_app.task(name="reconcore.start_search")
def start_search(search_id: str) -> dict:
    """Campaign entry point: build the plan, then dispatch it."""
    with session_scope() as db:
        search = db.get(Search, uuid.UUID(search_id))
        if search is None:
            return {"error": "search not found"}

        search.status = RunStatus.RUNNING.value
        search.started_at = _now()
        person = db.get(Person, search.person_id) if search.person_id else None

        plan = plan_search(
            db,
            target_type=search.target_type,
            target_value=search.target_value,
            depth=search.depth,
            differential=search.differential,
            person=person,
            options=search.params or {},
        )
        if not plan:
            search.status = RunStatus.SKIPPED.value
            search.finished_at = _now()
            search.stats = {"reason": "no enabled compatible plugin, or already executed"}
            return {"search_id": search_id, "dispatched": 0, "status": search.status}

        dispatched = _dispatch(
            db,
            plan,
            search=search,
            person=person,
            investigation_id=search.investigation_id,
            depth=1,
        )
        search.stats = {"dispatched": dispatched}
        if person is not None:
            person.last_search_at = _now()
            ident_service.add_timeline(
                db,
                person,
                kind="search_started",
                message=f"Search started on {search.target_type}: {search.target_value}",
                payload={"search_id": str(search.id), "plugins": [p["plugin"] for p in plan]},
            )
        return {"search_id": search_id, "dispatched": dispatched}


def _dispatch(db, plan: list[dict], *, search, person, investigation_id, depth: int) -> int:
    """Create the PluginRun rows and chain execution -> persistence."""
    count = 0
    for step in plan:
        plugin = registry.get(step["plugin"])
        if plugin is None:
            continue
        limits = registry.effective_limits(db, plugin)

        run = PluginRun(
            search_id=search.id if search else None,
            investigation_id=investigation_id,
            person_id=person.id if person else None,
            plugin=plugin.name,
            plugin_version=plugin.version,
            target_type=step["type"],
            target_value=step["value"][:500],
            normalized_target=step.get("normalized", step["value"])[:500],
            depth=depth,
            status=RunStatus.PENDING.value,
            started_at=_now(),
        )
        db.add(run)
        db.flush()

        context = dict(step.get("context") or {})
        # Secrets are decrypted here and passed to the task only.
        if plugin.requires_secrets:
            secrets = {}
            for key in plugin.requires_secrets:
                value = registry.get_secret(db, plugin.name, key)
                if value:
                    secrets[key] = value
            context["secrets"] = secrets

        signature = chain(
            plugin_execute.s(
                plugin.name,
                {
                    "type": step["type"],
                    "value": step["value"],
                    "normalized": step.get("normalized", ""),
                    "context": context,
                },
                limits,
            ).set(queue=plugin.queue),
            persist_plugin_result.s(str(run.id)).set(queue="default"),
        )
        result = signature.apply_async()
        run.celery_task_id = result.id
        run.status = RunStatus.RUNNING.value
        count += 1
    db.flush()
    return count


def _finish_search_if_done(db, run: PluginRun) -> None:
    if not run.search_id:
        return
    search = db.get(Search, run.search_id)
    if search is None:
        return
    # The session is created with autoflush=False, so this run's freshly
    # assigned status would otherwise be invisible to the query below and the
    # search would stay RUNNING forever.
    db.flush()
    pending = db.execute(
        select(PluginRun).where(
            PluginRun.search_id == search.id,
            PluginRun.status.in_([RunStatus.PENDING.value, RunStatus.RUNNING.value]),
        )
    ).scalars().first()
    if pending:
        return
    runs = db.execute(
        select(PluginRun).where(PluginRun.search_id == search.id)
    ).scalars().all()
    failed = [r for r in runs if r.status == RunStatus.FAILED.value]
    search.status = (
        RunStatus.SUCCESS.value
        if not failed
        else (RunStatus.PARTIAL.value if len(failed) < len(runs) else RunStatus.FAILED.value)
    )
    search.finished_at = _now()
    search.stats = {
        **(search.stats or {}),
        "runs": len(runs),
        "failed": len(failed),
        "items": sum(r.items_found or 0 for r in runs),
    }


# ------------------------------------------------------------- maintenance


@celery_app.task(name="reconcore.health_check")
def health_check() -> dict:
    """Check the availability of every enabled plugin (runs on the default queue).

    Note: the default worker does not have the third-party tools installed; the
    real probe is the one in the dedicated worker, triggered via `plugin_health`.
    """
    results = {}
    with session_scope() as db:
        for plugin in registry.enabled_plugins(db):
            task = plugin_health.s(plugin.name).set(queue=plugin.queue).apply_async()
            results[plugin.name] = task.id
    return results


@celery_app.task(name="reconcore.plugin_health")
def plugin_health(plugin_name: str) -> dict:
    plugin = registry.get(plugin_name)
    if plugin is None:
        return {"plugin": plugin_name, "ok": False, "message": "unknown plugin"}
    status = plugin.check_health()
    record_health.apply_async(args=[plugin_name, status.ok, status.message], queue="default")
    return {"plugin": plugin_name, **status.as_dict()}


@celery_app.task(name="reconcore.record_health")
def record_health(plugin_name: str, ok: bool, message: str) -> None:
    with session_scope() as db:
        registry.mark_health(db, plugin_name, ok, message)


@celery_app.task(name="reconcore.apply_retention")
def apply_retention() -> dict:
    """Apply the retention policy (permanent deletion)."""
    from app.core.config import settings
    from app.models.user import AuditLog

    removed = {"investigations": 0, "audit_logs": 0}
    with session_scope() as db:
        now = _now()
        if settings.data_retention_days > 0:
            limit = now - timedelta(days=settings.data_retention_days)
            stale = db.execute(
                select(Investigation).where(
                    Investigation.retention_until.is_(None),
                    Investigation.updated_at < limit,
                )
            ).scalars().all()
            for investigation in stale:
                db.delete(investigation)
                removed["investigations"] += 1

        expired = db.execute(
            select(Investigation).where(
                Investigation.retention_until.is_not(None),
                Investigation.retention_until < now,
            )
        ).scalars().all()
        for investigation in expired:
            db.delete(investigation)
            removed["investigations"] += 1

        if settings.audit_log_retention_days > 0:
            limit = now - timedelta(days=settings.audit_log_retention_days)
            old_logs = db.execute(
                select(AuditLog).where(AuditLog.at < limit)
            ).scalars().all()
            for log in old_logs:
                db.delete(log)
                removed["audit_logs"] += 1
    logger.info("Retention applied: %s", removed)
    return removed


@celery_app.task(name="reconcore.generate_variants")
def generate_variants(person_id: str, limit: int = 30) -> dict:
    """Generate username variants for a person (hypotheses only)."""
    from app.services.variants import combined

    with session_scope() as db:
        person = db.get(Person, uuid.UUID(person_id))
        if person is None:
            return {"error": "person not found"}
        index = ident_service.person_identifier_values(db, person.id)
        known = sorted(index.get(IdentifierType.USERNAME.value, set()))
        cities = sorted(index.get(IdentifierType.CITY.value, set()))
        variants = combined(
            person.first_name,
            person.last_name,
            known_usernames=known,
            birth_year=person.date_of_birth.year if person.date_of_birth else None,
            location_codes=cities,
            limit=limit,
        )
        created = 0
        for variant in variants:
            _, is_new = ident_service.add_username(
                db,
                person,
                value=variant.value,
                confidence=variant.confidence,
                is_variant=True,
                variant_rule=variant.rule,
                note="Automatically generated hypothesis, pending validation.",
                actor="variants",
            )
            created += int(is_new)
        return {"person_id": person_id, "generated": len(variants), "created": created}
