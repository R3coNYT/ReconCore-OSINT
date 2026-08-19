"""Common interface implemented by every OSINT plugin.

The contract:
  * `execute`      calls the external tool and returns its RAW output (never
                   interpreted). Runs inside the tool's dedicated worker.
  * `normalize`    converts raw output into internal `NormalizedItem` objects.
  * `validate`     filters or downgrades doubtful items before persistence.
  * `check_health` checks that the tool is installed and reachable.

A plugin never touches the database: it returns data. Persistence is handled by
the orchestrator, which guarantees that third-party tool output cannot write
into the system directly.
"""
from __future__ import annotations

import abc
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.models.enums import FindingType, IdentifierType, SourceKind


@dataclass
class Target:
    """A plugin target: one normalised identifier plus its context."""

    type: str
    value: str
    normalized: str = ""
    #: Non-identifying context useful to the plugin (options, region, sites).
    context: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.normalized:
            from app.services.normalization import normalize

            self.normalized = normalize(self.type, self.value)


@dataclass
class SourceRef:
    """Provenance attached to every normalised item."""

    kind: str = SourceKind.TOOL_OUTPUT.value
    url: str | None = None
    title: str | None = None
    description: str | None = None
    reliability: float = 0.7
    raw_reference: str | None = None

    def as_dict(self) -> dict:
        return {
            "kind": self.kind,
            "url": self.url,
            "title": self.title,
            "description": self.description,
            "reliability": self.reliability,
            "raw_reference": self.raw_reference,
        }


@dataclass
class NormalizedItem:
    """Item in internal format, ready to become a Finding."""

    kind: str
    title: str
    payload: dict[str, Any]
    source: SourceRef
    confidence: float = 0.5
    dedup_key: str | None = None
    #: Derived identifiers to inject into the case file (feeds recursion).
    derived_identifiers: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "kind": self.kind,
            "title": self.title,
            "payload": self.payload,
            "source": self.source.as_dict(),
            "confidence": self.confidence,
            "dedup_key": self.dedup_key,
            "derived_identifiers": self.derived_identifiers,
            "warnings": self.warnings,
        }


@dataclass
class RawResult:
    """Raw output of an external tool."""

    items: list[dict[str, Any]] = field(default_factory=list)
    logs: list[str] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    started_at: str = ""
    finished_at: str = ""
    duration_ms: int = 0

    def as_dict(self) -> dict:
        return {
            "items": self.items,
            "logs": self.logs,
            "meta": self.meta,
            "error": self.error,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_ms": self.duration_ms,
        }

    @classmethod
    def from_dict(cls, data: dict) -> RawResult:
        return cls(
            items=data.get("items", []),
            logs=data.get("logs", []),
            meta=data.get("meta", {}),
            error=data.get("error"),
            started_at=data.get("started_at", ""),
            finished_at=data.get("finished_at", ""),
            duration_ms=data.get("duration_ms", 0),
        )


@dataclass
class HealthStatus:
    ok: bool
    message: str
    version: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "message": self.message,
            "version": self.version,
            "details": self.details,
        }


class OSINTPlugin(abc.ABC):
    """Base class. Every plugin subclasses it and declares its metadata."""

    name: str = "base"
    version: str = "0.0.0"
    description: str = ""
    repository: str = ""
    license: str = ""
    #: Identifier types accepted as a target.
    supported_identifiers: list[str] = []
    #: Required secret keys (stored encrypted, never in clear text).
    requires_secrets: list[str] = []
    #: Dedicated Celery queue: each tool runs in ITS OWN container.
    queue: str = "default"
    #: Default quotas, overridable from the registry in the database.
    requests_per_minute: int = 30
    concurrency: int = 2
    timeout_seconds: int = 300
    retry_count: int = 1
    #: No plugin is ever switched on implicitly: enabling one is an explicit
    #: decision taken by an administrator.
    enabled_by_default: bool = False
    #: Warnings displayed in the UI before activation.
    risk_notes: list[str] = []

    # ------------------------------------------------------------------ API

    @abc.abstractmethod
    def check_health(self) -> HealthStatus:
        """Check that the underlying tool is available."""

    @abc.abstractmethod
    def execute(self, target: Target) -> RawResult:
        """Run the tool against the target and return its raw output."""

    @abc.abstractmethod
    def normalize(self, raw: RawResult, target: Target) -> list[NormalizedItem]:
        """Convert raw output into the internal format."""

    def validate(self, items: list[NormalizedItem], target: Target) -> list[NormalizedItem]:
        """Default filter: clamp confidences and drop empty items.

        Plugins may override this to add their own guardrails.
        """
        validated: list[NormalizedItem] = []
        for item in items:
            if not item.title:
                continue
            item.confidence = max(0.0, min(1.0, item.confidence))
            validated.append(item)
        return validated

    def supports(self, identifier_type: str) -> bool:
        return identifier_type in self.supported_identifiers

    # ------------------------------------------------------------- helpers

    def run(self, target: Target) -> tuple[RawResult, list[NormalizedItem]]:
        """Chain execute -> normalize -> validate while timing the run."""
        started = time.perf_counter()
        raw = self.execute(target)
        raw.started_at = raw.started_at or datetime.now(UTC).isoformat()
        raw.finished_at = datetime.now(UTC).isoformat()
        raw.duration_ms = int((time.perf_counter() - started) * 1000)
        if raw.error:
            return raw, []
        items = self.validate(self.normalize(raw, target), target)
        return raw, items

    def describe(self) -> dict:
        return {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "repository": self.repository,
            "license": self.license,
            "supported_identifiers": self.supported_identifiers,
            "requires_secrets": self.requires_secrets,
            "queue": self.queue,
            "enabled_by_default": self.enabled_by_default,
            "risk_notes": self.risk_notes,
            "limits": {
                "requests_per_minute": self.requests_per_minute,
                "concurrency": self.concurrency,
                "timeout_seconds": self.timeout_seconds,
                "retry_count": self.retry_count,
            },
        }


class PluginError(RuntimeError):
    """Plugin execution error (missing tool, timeout, unreadable output)."""


__all__ = [
    "FindingType",
    "HealthStatus",
    "IdentifierType",
    "NormalizedItem",
    "OSINTPlugin",
    "PluginError",
    "RawResult",
    "SourceKind",
    "SourceRef",
    "Target",
]
