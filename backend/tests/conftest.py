"""Test fixtures.

The suite runs on SQLite: the models use portable column types (`GUID`,
`JSONDict`, `TZDateTime`), so the integration tests need no infrastructure.
PostgreSQL remains the production target.

To run the same suite against PostgreSQL:
    RECONCORE_TEST_DATABASE_URL=postgresql+psycopg://user:pass@localhost/reconcore_test pytest
"""
from __future__ import annotations

import os
import tempfile
import uuid
from pathlib import Path

# --- Configuration BEFORE any application import ----------------------------
_DB_FILE = Path(tempfile.gettempdir()) / f"reconcore-test-{uuid.uuid4().hex}.sqlite3"
os.environ.setdefault(
    "DATABASE_URL_OVERRIDE",
    os.environ.get("RECONCORE_TEST_DATABASE_URL", f"sqlite:///{_DB_FILE}"),
)
os.environ.setdefault("SECRET_KEY", "test-secret-key-with-more-than-32-characters!!")
os.environ.setdefault(
    "SECRETS_ENCRYPTION_KEY", "5jMEuQK7lXQNvV0Gk3ub0xM8hXbfWSJqWtUuVv9wJ2E="
)
os.environ.setdefault("RECONCORE_ENV", "development")
os.environ.setdefault("SEARCH_PROVIDER", "none")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import app.models  # noqa: E402,F401  (registers the tables)
from app.db.base import Base  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402
from app.main import app as fastapi_app  # noqa: E402
from app.models.enums import UserRole  # noqa: E402
from app.models.user import User  # noqa: E402
from app.plugins import registry  # noqa: E402
from app.security.passwords import hash_password  # noqa: E402
from app.services.platforms import seed_platforms  # noqa: E402

ADMIN_PASSWORD = "Analyste!Test2026"


@pytest.fixture(scope="session", autouse=True)
def _schema():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        seed_platforms(db)
        registry.sync_registry(db)
        for email, role in (
            ("admin@reconcore-demo.fr", UserRole.ADMIN),
            ("analyst@reconcore-demo.fr", UserRole.ANALYST),
            ("reader@reconcore-demo.fr", UserRole.READ_ONLY),
        ):
            db.add(
                User(
                    email=email,
                    hashed_password=hash_password(ADMIN_PASSWORD),
                    role=role.value,
                )
            )
        db.commit()
    yield
    Base.metadata.drop_all(bind=engine)
    engine.dispose()
    _DB_FILE.unlink(missing_ok=True)


@pytest.fixture(autouse=True)
def _reset_rate_limits():
    """Rate limiting must not make the suite fail."""
    from app.security.ratelimit import reset_backend

    reset_backend()
    yield


@pytest.fixture
def client() -> TestClient:
    return TestClient(fastapi_app)


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
        session.commit()
    finally:
        session.close()


def _login(client: TestClient, email: str) -> dict[str, str]:
    response = client.post(
        "/api/v1/auth/login", json={"email": email, "password": ADMIN_PASSWORD}
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.fixture
def admin(client: TestClient) -> dict[str, str]:
    return _login(client, "admin@reconcore-demo.fr")


@pytest.fixture
def analyst(client: TestClient) -> dict[str, str]:
    return _login(client, "analyst@reconcore-demo.fr")


@pytest.fixture
def reader(client: TestClient) -> dict[str, str]:
    return _login(client, "reader@reconcore-demo.fr")


@pytest.fixture
def person(client: TestClient, analyst: dict[str, str]) -> dict:
    """One case file plus one person, ready to be enriched."""
    investigation = client.post(
        "/api/v1/investigations",
        json={
            "title": f"Test case file {uuid.uuid4().hex[:6]}",
            "entity_type": "PERSON",
            "legal_basis": "Automated test",
        },
        headers=analyst,
    ).json()

    created = client.post(
        f"/api/v1/investigations/{investigation['id']}/persons",
        json={"display_name": "Jean Dupont", "first_name": "Jean", "last_name": "Dupont"},
        headers=analyst,
    ).json()
    created["investigation"] = investigation
    return created
