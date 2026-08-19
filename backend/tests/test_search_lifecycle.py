"""Search campaign lifecycle.

Regression test for a bug found while running the real stack: because the
session is created with `autoflush=False`, the status just assigned to a run was
invisible to the closing query, so a search whose runs had all succeeded stayed
`RUNNING` forever.
"""
from __future__ import annotations

import uuid

from app.db.session import SessionLocal
from app.models.enums import RunStatus
from app.models.investigation import Person
from app.models.ops import PluginRun, Search
from app.workers.tasks import _finish_search_if_done


def _campaign(person_id: uuid.UUID, statuses: list[str]) -> tuple[uuid.UUID, list[uuid.UUID]]:
    with SessionLocal() as db:
        person = db.get(Person, person_id)
        search = Search(
            investigation_id=person.investigation_id,
            person_id=person.id,
            target_type="PHONE",
            target_value="+33612345678",
            status=RunStatus.RUNNING.value,
        )
        db.add(search)
        db.flush()
        run_ids = []
        for status in statuses:
            run = PluginRun(
                search_id=search.id,
                investigation_id=person.investigation_id,
                person_id=person.id,
                plugin="phoneinfoga",
                target_type="PHONE",
                target_value="+33612345678",
                normalized_target="+33612345678",
                status=status,
            )
            db.add(run)
            db.flush()
            run_ids.append(run.id)
        db.commit()
        return search.id, run_ids


def test_search_completes_when_its_last_run_succeeds(person: dict) -> None:
    person_id = uuid.UUID(person["id"])
    search_id, run_ids = _campaign(
        person_id, [RunStatus.SUCCESS.value, RunStatus.RUNNING.value]
    )

    with SessionLocal() as db:
        run = db.get(PluginRun, run_ids[1])
        # Mirrors persist_plugin_result: the status is set but not yet flushed.
        run.status = RunStatus.SUCCESS.value
        _finish_search_if_done(db, run)
        db.commit()

    with SessionLocal() as db:
        search = db.get(Search, search_id)

    assert search.status == RunStatus.SUCCESS.value
    assert search.finished_at is not None
    assert search.stats["runs"] == 2
    assert search.stats["failed"] == 0


def test_search_stays_running_while_a_run_is_pending(person: dict) -> None:
    person_id = uuid.UUID(person["id"])
    search_id, run_ids = _campaign(
        person_id, [RunStatus.SUCCESS.value, RunStatus.PENDING.value]
    )

    with SessionLocal() as db:
        _finish_search_if_done(db, db.get(PluginRun, run_ids[0]))
        db.commit()

    with SessionLocal() as db:
        assert db.get(Search, search_id).status == RunStatus.RUNNING.value


def test_partial_when_one_run_failed(person: dict) -> None:
    person_id = uuid.UUID(person["id"])
    search_id, run_ids = _campaign(
        person_id, [RunStatus.SUCCESS.value, RunStatus.RUNNING.value]
    )

    with SessionLocal() as db:
        run = db.get(PluginRun, run_ids[1])
        run.status = RunStatus.FAILED.value
        _finish_search_if_done(db, run)
        db.commit()

    with SessionLocal() as db:
        search = db.get(Search, search_id)

    assert search.status == RunStatus.PARTIAL.value
    assert search.stats["failed"] == 1
