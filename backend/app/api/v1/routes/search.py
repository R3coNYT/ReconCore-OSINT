"""Launching and monitoring searches (asynchronous jobs)."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import db_session, get_current_user, require_analyst
from app.models.enums import EntityType, IdentifierType, RunStatus
from app.models.investigation import Investigation, Person
from app.models.ops import PluginRun, Search
from app.models.user import User
from app.schemas.common import Message
from app.schemas.evidence import (
    PluginRunOut,
    SearchCreate,
    SearchDetail,
    SearchOut,
    SearchProgress,
)
from app.security import audit
from app.security.ratelimit import search_limiter
from app.services.normalization import normalize
from app.services.orchestration import compatible_plugins, plan_search

router = APIRouter(tags=["search"])

SCRATCH_TITLE = "Quick searches"


def scratch_investigation(db: Session, user: User) -> Investigation:
    """Per-user scratch case file for one-off searches.

    Even a "quick" search is attached to a case file: that guarantees every
    result keeps its provenance and falls under the retention policy.
    """
    title = f"{SCRATCH_TITLE} - {user.email}"
    investigation = db.execute(
        select(Investigation).where(
            Investigation.title == title, Investigation.owner_id == user.id
        )
    ).scalars().first()
    if investigation is None:
        investigation = Investigation(
            title=title,
            entity_type=EntityType.OTHER.value,
            description="Technical case file grouping one-off searches.",
            owner_id=user.id,
            automation_enabled=False,
        )
        db.add(investigation)
        db.flush()
    return investigation


@router.post("/searches", response_model=SearchOut, status_code=202)
def start_search(
    payload: SearchCreate,
    request: Request,
    db: Session = Depends(db_session),
    user: User = Depends(require_analyst),
) -> Search:
    """Create a search campaign and queue it."""
    if not search_limiter.allow(str(user.id)):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many searches launched. Wait before starting another.",
        )

    person = db.get(Person, payload.person_id) if payload.person_id else None
    if payload.person_id and person is None:
        raise HTTPException(status_code=404, detail="Person not found")

    if person is not None:
        investigation_id = person.investigation_id
    elif payload.investigation_id:
        investigation = db.get(Investigation, payload.investigation_id)
        if investigation is None:
            raise HTTPException(status_code=404, detail="Case file not found")
        investigation_id = investigation.id
    else:
        investigation_id = scratch_investigation(db, user).id

    options = dict(payload.options or {})
    if payload.plugins:
        options["plugins"] = payload.plugins
    if payload.force:
        options["force"] = True

    plan = plan_search(
        db,
        target_type=payload.target_type.value,
        target_value=payload.target_value,
        depth=payload.depth,
        differential=payload.differential,
        person=person,
        options=options,
    )
    if not plan:
        raise HTTPException(
            status_code=409,
            detail=(
                "No enabled compatible plugin, or the target was processed recently. "
                "Use force=true to run it again."
            ),
        )

    search = Search(
        investigation_id=investigation_id,
        person_id=person.id if person else None,
        initiated_by_id=user.id,
        label=f"{payload.target_type.value}: {payload.target_value}",
        target_type=payload.target_type.value,
        target_value=payload.target_value,
        depth=payload.depth,
        differential=payload.differential,
        params=options,
        status=RunStatus.PENDING.value,
    )
    db.add(search)
    db.flush()

    audit.record(
        db,
        action="search.started",
        user=user,
        object_type="search",
        object_id=search.id,
        message=f"Search {payload.target_type.value} = {payload.target_value} (depth {payload.depth})",
        detail={"plugins": [p["plugin"] for p in plan]},
        request=request,
    )

    # The transaction must be visible to the worker before dispatching.
    db.commit()

    from app.workers.tasks import start_search as start_search_task

    start_search_task.apply_async(args=[str(search.id)], queue="default")
    return search


@router.get("/searches", response_model=list[SearchOut])
def list_searches(
    db: Session = Depends(db_session),
    _: User = Depends(get_current_user),
    investigation_id: uuid.UUID | None = None,
    person_id: uuid.UUID | None = None,
    status_filter: str | None = Query(default=None, alias="status"),
    unattached: bool = Query(
        default=False,
        description="Only searches not attached to any person (quick searches).",
    ),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[Search]:
    query = select(Search).order_by(Search.created_at.desc())
    if unattached:
        query = query.where(Search.person_id.is_(None))
    if investigation_id:
        query = query.where(Search.investigation_id == investigation_id)
    if person_id:
        query = query.where(Search.person_id == person_id)
    if status_filter:
        query = query.where(Search.status == status_filter)
    return list(db.execute(query.limit(limit).offset(offset)).scalars().all())


@router.get("/searches/{search_id}", response_model=SearchDetail)
def get_search(
    search_id: uuid.UUID,
    db: Session = Depends(db_session),
    _: User = Depends(get_current_user),
) -> dict:
    search = db.get(Search, search_id)
    if search is None:
        raise HTTPException(status_code=404, detail="Search not found")
    runs = db.execute(
        select(PluginRun).where(PluginRun.search_id == search.id).order_by(PluginRun.created_at)
    ).scalars().all()
    return {
        **SearchOut.model_validate(search).model_dump(),
        "runs": [PluginRunOut.model_validate(r) for r in runs],
    }


@router.get("/searches/{search_id}/progress", response_model=SearchProgress)
def search_progress(
    search_id: uuid.UUID,
    db: Session = Depends(db_session),
    _: User = Depends(get_current_user),
) -> dict:
    """Progress state, consumed by the frontend progress bars."""
    search = db.get(Search, search_id)
    if search is None:
        raise HTTPException(status_code=404, detail="Search not found")
    runs = list(
        db.execute(
            select(PluginRun).where(PluginRun.search_id == search.id).order_by(PluginRun.created_at)
        ).scalars().all()
    )
    finished = [
        r
        for r in runs
        if r.status in {RunStatus.SUCCESS.value, RunStatus.FAILED.value, RunStatus.SKIPPED.value}
    ]
    return {
        "search_id": search.id,
        "status": search.status,
        "total_runs": len(runs),
        "finished_runs": len(finished),
        "progress": round(len(finished) / len(runs), 3) if runs else 0.0,
        "runs": [PluginRunOut.model_validate(r) for r in runs],
    }


@router.get("/plugin-runs/{run_id}", response_model=PluginRunOut)
def get_run(
    run_id: uuid.UUID,
    db: Session = Depends(db_session),
    _: User = Depends(get_current_user),
) -> PluginRun:
    run = db.get(PluginRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return run


@router.get("/searches/preview/plan")
def preview_plan(
    target_type: IdentifierType,
    target_value: str,
    db: Session = Depends(db_session),
    _: User = Depends(get_current_user),
    person_id: uuid.UUID | None = None,
    force: bool = False,
) -> dict:
    """Show the analyst what would run, without running anything."""
    person = db.get(Person, person_id) if person_id else None
    plan = plan_search(
        db,
        target_type=target_type.value,
        target_value=target_value,
        person=person,
        options={"force": force},
    )
    return {
        "normalized_value": normalize(target_type.value, target_value),
        "planned": plan,
        "compatible_plugins": compatible_plugins(db, target_type.value),
        "note": (
            "Nothing was launched. Plugins already run recently against this "
            "target are excluded (differential search)."
        ),
    }


@router.post("/persons/{person_id}/generate-variants", response_model=Message, status_code=202)
def generate_variants(
    person_id: uuid.UUID,
    db: Session = Depends(db_session),
    user: User = Depends(require_analyst),
    limit: int = Query(default=30, ge=1, le=200),
) -> Message:
    person = db.get(Person, person_id)
    if person is None:
        raise HTTPException(status_code=404, detail="Person not found")
    db.commit()

    from app.workers.tasks import generate_variants as task

    task.apply_async(args=[str(person_id), limit], queue="default")
    return Message(detail="Variant generation started (hypotheses only).")
