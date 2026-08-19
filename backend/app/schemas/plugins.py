"""Plugin registry schemas."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class PluginLimits(BaseModel):
    requests_per_minute: int
    concurrency: int
    timeout_seconds: int
    retry_count: int


class PluginOut(BaseModel):
    name: str
    version: str
    description: str | None = None
    repository: str | None = None
    license: str | None = None
    enabled: bool
    supported_identifiers: list[str] = []
    requires_secrets: list[str] = []
    #: {key: present or not} - the value is never exposed.
    secrets_configured: dict[str, bool] = {}
    risk_level: str
    risk_notes: list[str] = []
    last_audit_at: datetime | None = None
    health_status: str | None = None
    health_message: str | None = None
    health_checked_at: datetime | None = None
    limits: PluginLimits
    queue: str


class PluginToggle(BaseModel):
    enabled: bool
    acknowledge_risks: bool = Field(
        default=False,
        description=(
            "Required to enable a plugin that carries warnings (e.g. Toutatis)."
        ),
    )


class PluginLimitsUpdate(BaseModel):
    requests_per_minute: int | None = Field(default=None, ge=1, le=600)
    concurrency: int | None = Field(default=None, ge=1, le=16)
    timeout_seconds: int | None = Field(default=None, ge=10, le=3600)
    retry_count: int | None = Field(default=None, ge=0, le=5)


class PluginSecretSet(BaseModel):
    key: str
    value: str = Field(min_length=1)


class PluginSecretOut(BaseModel):
    plugin: str
    key: str
    hint: str | None = None
    updated_at: datetime | None = None


class PluginAuditOut(BaseModel):
    plugin: str
    repository: str | None = None
    license: str | None = None
    version: str | None = None
    last_upstream_update: str | None = None
    last_reviewed: str | None = None
    risk_level: str
    network_access: str
    filesystem_access: str
    subprocess: str
    dynamic_downloads: str
    privileged_operations: str
    docker_socket: str
    hardcoded_secrets: str
    suspicious_behavior: str
    files_scanned: int
    dependencies: list[str]
    dockerfiles: list[str]
    github_workflows: list[str]
    shell_scripts: list[str]
    signals: list[dict]
    errors: list[str]
    generated_at: str
    disclaimer: str


class PluginHealthOut(BaseModel):
    plugin: str
    ok: bool
    message: str
    version: str | None = None
    details: dict = {}
