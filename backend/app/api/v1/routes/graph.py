"""Identity graph, platforms, exports and dashboard."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import db_session, get_current_user, require_admin, require_analyst
from app.models.enums import FindingStatus, RunStatus
from app.models.evidence import Contradiction, Finding, Source
from app.models.identity import Platform, SocialProfile, Username
from app.models.investigation import Investigation, Person
from app.models.ops import PluginRun, Search
from app.models.user import User
from app.schemas.evidence import GraphOut
from app.schemas.identity import PlatformCreate, PlatformOut
from app.security import audit
from app.security.ratelimit import export_limiter
from app.services import export as export_service
from app.services import graph as graph_service
from app.services import platforms as platform_service

router = APIRouter(tags=["graph & exports"])


@router.get("/investigations/{investigation_id}/graph", response_model=GraphOut)
def investigation_graph(
    investigation_id: uuid.UUID,
    db: Session = Depends(db_session),
    _: User = Depends(get_current_user),
    person_id: uuid.UUID | None = None,
    min_confidence: float = Query(default=0.0, ge=0.0, le=1.0),
    types: str | None = Query(
        default=None,
        description="Node types to include, comma-separated.",
    ),
) -> dict:
    investigation = db.get(Investigation, investigation_id)
    if investigation is None:
        raise HTTPException(status_code=404, detail="Case file not found")
    include = {t.strip() for t in types.split(",")} if types else None
    return graph_service.build_graph(
        db,
        investigation_id,
        person_id=person_id,
        min_confidence=min_confidence,
        include_types=include,
    )


@router.get("/persons/{person_id}/graph", response_model=GraphOut)
def person_graph(
    person_id: uuid.UUID,
    db: Session = Depends(db_session),
    _: User = Depends(get_current_user),
    min_confidence: float = Query(default=0.0, ge=0.0, le=1.0),
) -> dict:
    person = db.get(Person, person_id)
    if person is None:
        raise HTTPException(status_code=404, detail="Person not found")
    return graph_service.build_graph(
        db, person.investigation_id, person_id=person.id, min_confidence=min_confidence
    )


# ------------------------------------------------------------------ exports


@router.get("/persons/{person_id}/export")
def export_person(
    person_id: uuid.UUID,
    request: Request,
    db: Session = Depends(db_session),
    user: User = Depends(require_analyst),
    format: str = Query(default="json", pattern="^(json|csv|pdf)$"),
) -> Response:
    """Full case file export (JSON, CSV or PDF)."""
    if not export_limiter.allow(str(user.id)):
        raise HTTPException(status_code=429, detail="Too many exports. Try again later.")

    person = db.get(Person, person_id)
    if person is None:
        raise HTTPException(status_code=404, detail="Person not found")

    case_file = export_service.build_case_file(db, person)
    audit.record(
        db,
        action="case_file.exported",
        user=user,
        object_type="person",
        object_id=person.id,
        message=f"{format.upper()} export of case file {person.display_name}",
        request=request,
    )

    if format == "json":
        content, media = export_service.to_json(case_file), "application/json"
    elif format == "csv":
        content, media = export_service.to_csv(case_file), "text/csv; charset=utf-8"
    else:
        content, media = export_service.to_pdf(case_file), "application/pdf"

    filename = export_service.export_filename(person, format)
    return Response(
        content=content,
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ----------------------------------------------------------------- platforms


@router.get("/platforms", response_model=list[PlatformOut])
def list_platforms(
    db: Session = Depends(db_session),
    _: User = Depends(get_current_user),
    category: str | None = None,
    enabled_only: bool = False,
) -> list[Platform]:
    query = select(Platform).order_by(Platform.name)
    if category:
        query = query.where(Platform.category == category)
    if enabled_only:
        query = query.where(Platform.enabled.is_(True))
    return list(db.execute(query).scalars().all())


@router.post("/platforms", response_model=PlatformOut, status_code=201)
def create_platform(
    payload: PlatformCreate,
    db: Session = Depends(db_session),
    _: User = Depends(require_admin),
) -> Platform:
    slug = platform_service.slugify(payload.name)
    if platform_service.by_slug(db, slug):
        raise HTTPException(status_code=409, detail="Platform already exists")
    platform = Platform(
        name=payload.name,
        slug=slug,
        category=payload.category.value,
        base_url=payload.base_url,
        profile_url_template=payload.profile_url_template,
        icon=payload.icon,
        enabled=payload.enabled,
    )
    db.add(platform)
    db.flush()
    return platform


# ----------------------------------------------------------------- dashboard


@router.get("/dashboard")
def dashboard(
    db: Session = Depends(db_session), _: User = Depends(get_current_user)
) -> dict:
    """Key figures and recent activity displayed on the home page."""

    def count(model, *conditions) -> int:
        query = select(func.count()).select_from(model)
        for condition in conditions:
            query = query.where(condition)
        return int(db.execute(query).scalar_one())

    recent_searches = db.execute(
        select(Search).order_by(Search.created_at.desc()).limit(10)
    ).scalars().all()
    recent_findings = db.execute(
        select(Finding)
        .where(Finding.status == FindingStatus.NEW.value)
        .order_by(Finding.discovered_at.desc().nullslast())
        .limit(10)
    ).scalars().all()

    by_plugin = db.execute(
        select(PluginRun.plugin, func.count(), func.sum(PluginRun.items_found))
        .group_by(PluginRun.plugin)
    ).all()

    return {
        "counts": {
            "investigations": count(Investigation),
            "persons": count(Person),
            "usernames": count(Username),
            "social_profiles": count(SocialProfile),
            "findings": count(Finding),
            "new_findings": count(Finding, Finding.status == FindingStatus.NEW.value),
            "sources": count(Source),
            "open_contradictions": count(Contradiction, Contradiction.resolved.is_(False)),
            "running_searches": count(
                Search, Search.status.in_([RunStatus.PENDING.value, RunStatus.RUNNING.value])
            ),
        },
        "plugin_activity": [
            {"plugin": plugin, "runs": runs, "items": int(items or 0)}
            for plugin, runs, items in by_plugin
        ],
        "recent_searches": [
            {
                "id": str(s.id),
                "label": s.label,
                "status": s.status,
                "created_at": s.created_at.isoformat(),
            }
            for s in recent_searches
        ],
        "recent_findings": [
            {
                "id": str(f.id),
                "title": f.title,
                "type": f.type,
                "plugin": f.plugin,
                "confidence": f.confidence,
                "person_id": str(f.person_id) if f.person_id else None,
            }
            for f in recent_findings
        ],
    }
