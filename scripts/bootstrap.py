#!/usr/bin/env python3
"""Full initialisation: tables, platforms, plugins, admin account.

Run once, from the repository root, with the backend virtualenv active:
    python scripts/bootstrap.py
Inside Docker, prefer the CLI:
    docker compose exec api python -m app.cli db init
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from sqlalchemy import select  # noqa: E402

import app.models  # noqa: E402,F401  (registers the tables)
from app.core.config import settings  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.session import engine, session_scope  # noqa: E402
from app.models.enums import UserRole  # noqa: E402
from app.models.user import User  # noqa: E402
from app.plugins import registry  # noqa: E402
from app.security.passwords import hash_password, validate_password_strength  # noqa: E402
from app.services.platforms import seed_platforms  # noqa: E402


def main() -> int:
    Base.metadata.create_all(bind=engine)
    print("Tables created / verified.")

    with session_scope() as db:
        created = seed_platforms(db)
        print(f"{created} platform(s) inserted.")

        entries = registry.sync_registry(db)
        print(f"{len(entries)} plugin(s) registered:")
        for entry in entries:
            print(f"  - {entry.name} ({'enabled' if entry.enabled else 'disabled'})")

        email = settings.first_admin_email.lower()
        existing = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
        if existing:
            print(f"Admin account already present: {email}")
            return 0

        password = settings.first_admin_password
        if not password:
            print(
                "FIRST_ADMIN_PASSWORD is not set: no account created.\n"
                "Create it manually: osint user create --email ... --role ADMIN"
            )
            return 1
        problems = validate_password_strength(password)
        if problems:
            print("FIRST_ADMIN_PASSWORD is too weak: " + ", ".join(problems))
            return 1

        db.add(
            User(
                email=email,
                full_name="Administrateur",
                hashed_password=hash_password(password),
                role=UserRole.ADMIN.value,
            )
        )
        print(f"Administrator account created: {email}")
        print("Change this password on first login.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
