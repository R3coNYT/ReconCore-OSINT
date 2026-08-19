"""Column-width guardrails on ingested data.

Regression test for a bug found while running the real stack: PhoneInfoga's
`googlesearch` scanner produces dorks whose dedup key exceeds the
`varchar(300)` column, which made the whole persistence task fail.
"""
from __future__ import annotations

import uuid

from app.db.session import SessionLocal
from app.models.enums import RunStatus
from app.models.investigation import Person
from app.models.ops import PluginRun, SearchResult
from app.plugins.base import NormalizedItem, SourceKind, SourceRef
from app.services.ingest import MAX_DEDUP_KEY, bounded_dedup_key, ingest_items


def test_short_keys_are_left_untouched() -> None:
    assert bounded_dedup_key("social:github:jdupont") == "social:github:jdupont"
    assert bounded_dedup_key(None) is None
    assert bounded_dedup_key("") == ""


def test_long_keys_are_bounded_and_stay_unique() -> None:
    base = "dork:+33612345678:" + "site:example.org OR " * 40
    first = bounded_dedup_key(base + "alpha")
    second = bounded_dedup_key(base + "beta")

    assert len(first) <= MAX_DEDUP_KEY
    assert len(second) <= MAX_DEDUP_KEY
    assert first != second, "two different long keys must not collapse together"
    assert bounded_dedup_key(base + "alpha") == first, "hashing must be stable"


def test_ingesting_a_very_long_dork_does_not_fail(person: dict) -> None:
    """A plugin item with an oversized dedup key must persist cleanly."""
    long_query = '"+33612345678" ' + "OR (site:example.org inurl:profile) " * 20
    item = NormalizedItem(
        kind="search_query",
        title="Suggested search: very long dork",
        payload={"query": long_query, "url": "https://example.org/s", "executed": False},
        source=SourceRef(kind=SourceKind.USER_HYPOTHESIS.value, reliability=0.2),
        confidence=0.1,
        dedup_key=f"dork:+33612345678:{long_query}",
    )
    assert len(item.dedup_key) > MAX_DEDUP_KEY

    with SessionLocal() as db:
        target = db.get(Person, uuid.UUID(person["id"]))
        run = PluginRun(
            investigation_id=target.investigation_id,
            person_id=target.id,
            plugin="phoneinfoga",
            target_type="PHONE",
            target_value="+33612345678",
            normalized_target="+33612345678",
            status=RunStatus.SUCCESS.value,
        )
        db.add(run)
        db.flush()

        stats = ingest_items(db, run, [item.as_dict()], person=target)
        db.commit()

        stored = db.query(SearchResult).filter(SearchResult.run_id == run.id).all()

    assert stats["findings_created"] == 1
    assert stored and len(stored[0].dedup_key) <= MAX_DEDUP_KEY
