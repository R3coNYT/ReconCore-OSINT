"""FastAPI dependencies: authentication, RBAC, object lookup."""
from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.enums import UserRole
from app.models.investigation import Investigation, Person
from app.models.user import User
from app.security.tokens import TokenError, decode_access_token

bearer = HTTPBearer(auto_error=False)


def db_session() -> Iterator[Session]:
    yield from get_db()


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(db_session),
) -> User:
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        payload = decode_access_token(credentials.credentials)
    except TokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    user = db.get(User, uuid.UUID(payload["sub"]))
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="Unknown or deactivated account")

    # Global logout: tokens issued before this date are worthless.
    if user.tokens_valid_after and payload.get("iat"):
        from datetime import datetime

        issued = datetime.fromtimestamp(payload["iat"], tz=UTC)
        if issued < user.tokens_valid_after:
            raise HTTPException(status_code=401, detail="Session revoked")
    return user


class RequireRole:
    """RBAC dependency: `Depends(RequireRole(UserRole.ADMIN))`."""

    #: ADMIN inherits ANALYST rights, which inherit READ_ONLY.
    HIERARCHY = {
        UserRole.READ_ONLY.value: 0,
        UserRole.ANALYST.value: 1,
        UserRole.ADMIN.value: 2,
    }

    def __init__(self, minimum: UserRole) -> None:
        self.minimum = minimum

    def __call__(self, user: User = Depends(get_current_user)) -> User:
        if self.HIERARCHY.get(user.role, -1) < self.HIERARCHY[self.minimum.value]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"{self.minimum.value} role required",
            )
        return user


require_admin = RequireRole(UserRole.ADMIN)
require_analyst = RequireRole(UserRole.ANALYST)
require_reader = RequireRole(UserRole.READ_ONLY)


def get_investigation(
    investigation_id: uuid.UUID,
    db: Session = Depends(db_session),
    user: User = Depends(get_current_user),
) -> Investigation:
    investigation = db.get(Investigation, investigation_id)
    if investigation is None:
        raise HTTPException(status_code=404, detail="Case file not found")
    return investigation


def get_person(
    person_id: uuid.UUID,
    db: Session = Depends(db_session),
    user: User = Depends(get_current_user),
) -> Person:
    person = db.get(Person, person_id)
    if person is None:
        raise HTTPException(status_code=404, detail="Person not found")
    return person


def client_request(request: Request) -> Request:
    return request
