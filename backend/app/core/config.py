"""Central configuration, loaded from the environment (12-factor)."""
from __future__ import annotations

import os
from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"), env_file_encoding="utf-8", extra="ignore"
    )

    # --- General ---
    reconcore_env: str = "development"
    log_level: str = "INFO"
    api_v1_prefix: str = "/api/v1"
    project_name: str = "ReconCore OSINT"
    backend_cors_origins: str = "http://localhost:5173"

    # --- Security ---
    secret_key: str = Field(default="dev-insecure-secret-key-change-me-please-32")
    secrets_encryption_key: str = ""
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 7
    first_admin_email: str = "admin@example.org"
    first_admin_password: str = ""

    # --- Database ---
    postgres_user: str = "reconcore"
    postgres_password: str = "reconcore"
    postgres_db: str = "reconcore"
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    database_url_override: str = ""

    # --- Redis / Celery ---
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/2"

    # --- Retention ---
    data_retention_days: int = 0
    audit_log_retention_days: int = 365

    # --- Plugins ---
    osint_http_proxy: str = ""
    osint_https_proxy: str = ""
    plugin_default_timeout: int = 300
    toutatis_enabled: bool = False
    #: URL of the PhoneInfoga container in REST mode (e.g. http://phoneinfoga:5000).
    phoneinfoga_api_url: str = ""

    # --- Web search ---
    search_provider: str = "none"
    searxng_base_url: str = ""
    serpapi_api_key: str = ""
    brave_search_api_key: str = ""

    @field_validator("secret_key")
    @classmethod
    def _check_secret(cls, v: str) -> str:
        if len(v) < 32:
            raise ValueError("SECRET_KEY must be at least 32 characters long")
        return v

    @property
    def is_production(self) -> bool:
        return self.reconcore_env.lower() in {"production", "prod"}

    @property
    def database_url(self) -> str:
        if self.database_url_override:
            return self.database_url_override
        return (
            f"postgresql+psycopg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.backend_cors_origins.split(",") if o.strip()]

    @property
    def proxies(self) -> dict[str, str]:
        p: dict[str, str] = {}
        if self.osint_http_proxy:
            p["http://"] = self.osint_http_proxy
        if self.osint_https_proxy:
            p["https://"] = self.osint_https_proxy
        return p


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()

# Guardrail: refuse to start in production with development defaults.
if settings.is_production:
    if settings.secret_key.startswith("dev-insecure"):
        raise RuntimeError("the default SECRET_KEY is not allowed in production")
    if not settings.secrets_encryption_key:
        raise RuntimeError("SECRETS_ENCRYPTION_KEY is required in production")
    if os.environ.get("POSTGRES_PASSWORD", "") in {"", "reconcore", "postgres"}:
        raise RuntimeError("a default POSTGRES_PASSWORD is not allowed in production")
