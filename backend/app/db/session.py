"""SQLAlchemy session factory (synchronous, shared by the API and workers)."""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings


def _make_engine():
    url = settings.database_url
    if url.startswith("sqlite"):
        # Tests and local tooling only: no connection pool, and access allowed
        # from the test client's threads.
        engine = create_engine(
            url, connect_args={"check_same_thread": False}, future=True
        )

        @event.listens_for(engine, "connect")
        def _enable_foreign_keys(dbapi_connection, _record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        return engine

    return create_engine(
        url, pool_pre_ping=True, pool_size=10, max_overflow=20, future=True
    )


engine = _make_engine()

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    """FastAPI dependency: one session per request."""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional context for Celery workers and the CLI."""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
