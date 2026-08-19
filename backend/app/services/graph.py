"""Identity graph construction (in a format Cytoscape.js can consume)."""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.evidence import Relationship
from app.models.identity import Identifier, Platform, SocialProfile, Username
from app.models.investigation import Organization, Person

#: Colour and shape are handled by the frontend; the backend supplies the type.
NODE_TYPES = (
    "person",
    "identifier",
    "username",
    "social_profile",
    "platform",
    "organization",
)


def build_graph(
    db: Session,
    investigation_id: uuid.UUID,
    *,
    person_id: uuid.UUID | None = None,
    min_confidence: float = 0.0,
    include_types: set[str] | None = None,
) -> dict:
    """Return {nodes, edges, stats} for a case file or a single person."""
    include = include_types or set(NODE_TYPES)
    nodes: dict[str, dict] = {}
    edges: list[dict] = []

    def add_node(node_type: str, node_id, label: str, **data) -> None:
        if node_type not in include:
            return
        key = f"{node_type}:{node_id}"
        if key not in nodes:
            nodes[key] = {
                "id": key,
                "type": node_type,
                "label": label,
                "ref": str(node_id),
                **data,
            }

    persons_query = select(Person).where(Person.investigation_id == investigation_id)
    if person_id:
        persons_query = persons_query.where(Person.id == person_id)
    persons = db.execute(persons_query).scalars().all()
    person_ids = {p.id for p in persons}

    for person in persons:
        add_node(
            "person",
            person.id,
            person.display_name,
            confidence=person.confidence_score,
            status="ROOT",
        )

    for identifier in db.execute(
        select(Identifier).where(Identifier.investigation_id == investigation_id)
    ).scalars().all():
        if person_id and identifier.person_id not in person_ids:
            continue
        if identifier.confidence < min_confidence:
            continue
        add_node(
            "identifier",
            identifier.id,
            identifier.value,
            subtype=identifier.type,
            confidence=identifier.confidence,
            status=identifier.status,
        )

    for username in db.execute(
        select(Username).where(Username.investigation_id == investigation_id)
    ).scalars().all():
        if person_id and username.person_id not in person_ids:
            continue
        if username.confidence < min_confidence:
            continue
        add_node(
            "username",
            username.id,
            username.value,
            confidence=username.confidence,
            status=username.status,
            is_variant=username.is_variant,
        )

    for profile in db.execute(
        select(SocialProfile).where(SocialProfile.investigation_id == investigation_id)
    ).scalars().all():
        if person_id and profile.person_id not in person_ids:
            continue
        if profile.confidence < min_confidence:
            continue
        platform = db.get(Platform, profile.platform_id) if profile.platform_id else None
        add_node(
            "social_profile",
            profile.id,
            f"{platform.name if platform else '?'}/{profile.username}",
            confidence=profile.confidence,
            status=profile.status,
            url=profile.url,
        )
        if platform:
            add_node("platform", platform.id, platform.name, category=platform.category)

    for organization in db.execute(
        select(Organization).where(Organization.investigation_id == investigation_id)
    ).scalars().all():
        add_node("organization", organization.id, organization.name)

    for relationship in db.execute(
        select(Relationship).where(Relationship.investigation_id == investigation_id)
    ).scalars().all():
        if relationship.confidence < min_confidence:
            continue
        source_key = f"{relationship.source_type}:{relationship.source_ref}"
        target_key = f"{relationship.target_type}:{relationship.target_ref}"
        if source_key not in nodes or target_key not in nodes:
            continue
        edges.append(
            {
                "id": str(relationship.id),
                "source": source_key,
                "target": target_key,
                "type": relationship.type,
                "confidence": relationship.confidence,
                "status": relationship.status,
                "note": relationship.note,
            }
        )

    counts: dict[str, int] = {}
    for node in nodes.values():
        counts[node["type"]] = counts.get(node["type"], 0) + 1

    return {
        "nodes": list(nodes.values()),
        "edges": edges,
        "stats": {"nodes": len(nodes), "edges": len(edges), "by_type": counts},
    }
