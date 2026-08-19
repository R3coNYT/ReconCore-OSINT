"""Authentication and account-management schemas."""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.models.enums import UserRole
from app.schemas.common import ORMModel
from app.security.passwords import validate_password_strength


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_at: datetime


class RefreshRequest(BaseModel):
    refresh_token: str


class UserOut(ORMModel):
    id: uuid.UUID
    email: str
    full_name: str | None = None
    role: str
    is_active: bool
    last_login_at: datetime | None = None
    created_at: datetime


class UserCreate(BaseModel):
    email: EmailStr
    password: str
    full_name: str | None = None
    role: UserRole = UserRole.ANALYST

    @field_validator("password")
    @classmethod
    def _strong(cls, value: str) -> str:
        problems = validate_password_strength(value)
        if problems:
            raise ValueError("Password too weak: " + ", ".join(problems))
        return value


class UserUpdate(BaseModel):
    full_name: str | None = None
    role: UserRole | None = None
    is_active: bool | None = None


class PasswordChange(BaseModel):
    current_password: str
    new_password: str = Field(min_length=12)

    @field_validator("new_password")
    @classmethod
    def _strong(cls, value: str) -> str:
        problems = validate_password_strength(value)
        if problems:
            raise ValueError("Password too weak: " + ", ".join(problems))
        return value


class AuditLogOut(ORMModel):
    id: uuid.UUID
    at: datetime
    user_email: str | None = None
    action: str
    object_type: str | None = None
    object_id: str | None = None
    ip_address: str | None = None
    message: str | None = None
    detail: dict | None = None
