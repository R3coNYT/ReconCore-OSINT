"""Holehe plugin: services where an email address is in use.

Upstream: https://github.com/megadose/holehe (GPL-3.0)
Installed in the dedicated worker with `pip install holehe`.

Holehe queries the public account-recovery endpoints of various services. Some
of them return PARTIALLY OBFUSCATED data (recovery email or phone). Those items
are stored verbatim as obfuscated hints (`recovery_email_obfuscated`,
`recovery_phone_obfuscated`): they are NEVER turned into certain identifiers,
and never "un-masked".
"""
from __future__ import annotations

import json

from app.plugins.base import (
    FindingType,
    HealthStatus,
    IdentifierType,
    NormalizedItem,
    OSINTPlugin,
    RawResult,
    SourceKind,
    SourceRef,
    Target,
)
from app.plugins.runner import python_executable, run_command

DRIVER = "app.plugins.holehe.driver"


class HolehePlugin(OSINTPlugin):
    name = "holehe"
    version = "1.0.0"
    description = "Detects the services where an email address is in use."
    repository = "https://github.com/megadose/holehe"
    license = "GPL-3.0"
    supported_identifiers = [IdentifierType.EMAIL.value]
    queue = "holehe"
    requests_per_minute = 6
    concurrency = 1
    timeout_seconds = 420
    #: Registered on startup, but never switched on implicitly: enabling a
    #: plugin stays an explicit decision (installer, CLI or UI).
    enabled_by_default = False
    risk_notes = [
        "Queries account-recovery forms: keep the request rate low so the "
        "services' abuse protections are not triggered.",
        "Obfuscated values returned stay hints, never facts.",
    ]

    def check_health(self) -> HealthStatus:
        result = run_command(
            [python_executable(), "-c", "import holehe, sys; print(getattr(holehe,'__version__','installed'))"],
            timeout=60,
        )
        if result.ok:
            return HealthStatus(
                ok=True, message="holehe available", version=result.stdout.strip() or None
            )
        return HealthStatus(
            ok=False,
            message=(
                "holehe not found in this worker. Install it: pip install holehe. "
                f"Detail: {result.stderr.strip()[:300]}"
            ),
        )

    def execute(self, target: Target) -> RawResult:
        raw = RawResult()
        email = target.normalized or target.value
        if "@" not in email:
            raw.error = "invalid email address"
            return raw

        raw.logs.append(f"[INFO] Starting Holehe for {email}")
        result = run_command(
            [
                python_executable(),
                "-m",
                DRIVER,
                email,
                "--concurrency",
                str(int(target.context.get("concurrency", 10))),
                "--module-timeout",
                str(float(target.context.get("module_timeout", 20))),
            ],
            timeout=target.context.get("timeout") or self.timeout_seconds,
        )

        if result.timed_out:
            raw.error = "Holehe timed out"
            return raw

        payload = _last_json(result.stdout)
        if payload is None:
            raw.error = (
                "unreadable Holehe output. "
                f"stderr={result.stderr.strip()[:500]}"
            )
            return raw
        if payload.get("error"):
            raw.error = str(payload["error"])[:1000]
            return raw

        results = payload.get("results", [])
        used = [r for r in results if r.get("exists")]
        rate_limited = [r for r in results if r.get("rateLimit")]

        raw.items = results
        raw.meta = {
            "modules_total": payload.get("modules_total"),
            "modules_errors": payload.get("modules_errors", [])[:50],
            "used_count": len(used),
            "rate_limited_count": len(rate_limited),
        }
        raw.logs += [
            f"[INFO] {payload.get('modules_total', 0)} modules executed",
            f"[INFO] {len(used)} service(s) with an existing account",
            f"[INFO] {len(rate_limited)} service(s) rate-limited (inconclusive)",
        ]
        return raw

    def normalize(self, raw: RawResult, target: Target) -> list[NormalizedItem]:
        email = target.normalized or target.value
        items: list[NormalizedItem] = []

        for entry in raw.items:
            service = (entry.get("name") or "").strip()
            if not service:
                continue
            exists = entry.get("exists")
            rate_limited = entry.get("rateLimit")
            domain = entry.get("domain") or ""

            if rate_limited or exists is None:
                # Inconclusive: kept as a trace, with no conclusion drawn.
                items.append(
                    NormalizedItem(
                        kind=FindingType.ACCOUNT_EXISTS.value,
                        title=f"{service}: inconclusive check",
                        payload={
                            "service": service,
                            "domain": domain,
                            "result": "inconclusive",
                            "reason": "rate_limit" if rate_limited else "unknown",
                        },
                        source=SourceRef(
                            kind=SourceKind.TOOL_OUTPUT.value,
                            title=f"Holehe - {service}",
                            description="Service protected or unavailable at the time of the check.",
                            reliability=0.30,
                            raw_reference=f"holehe:{service}",
                        ),
                        confidence=0.0,
                        dedup_key=f"holehe:{service.lower()}:{email}",
                    )
                )
                continue

            if not exists:
                continue

            items.append(
                NormalizedItem(
                    kind=FindingType.ACCOUNT_EXISTS.value,
                    title=f"{service}: account linked to {email}",
                    payload={
                        "service": service,
                        "domain": domain,
                        "email": email,
                        "result": "used",
                        "method": entry.get("method"),
                    },
                    source=SourceRef(
                        kind=SourceKind.TOOL_OUTPUT.value,
                        url=f"https://{domain}" if domain else None,
                        title=f"Holehe - {service}",
                        description=(
                            "The service reports that an account exists for this address."
                        ),
                        reliability=0.75,
                        raw_reference=f"holehe:{service}",
                    ),
                    confidence=0.65,
                    dedup_key=f"holehe:{service.lower()}:{email}",
                )
            )

            # --- Obfuscated hints: stored verbatim, never reconstructed ---
            recovery_email = entry.get("emailrecovery")
            if recovery_email:
                items.append(
                    NormalizedItem(
                        kind=FindingType.OBFUSCATED_CONTACT.value,
                        title=f"{service}: obfuscated recovery email",
                        payload={
                            "service": service,
                            "recovery_email_obfuscated": str(recovery_email).strip(),
                            "certain": False,
                        },
                        source=SourceRef(
                            kind=SourceKind.TOOL_OUTPUT.value,
                            title=f"Holehe - {service}",
                            description="Obfuscated hint provided by the service.",
                            reliability=0.70,
                            raw_reference=f"holehe:{service}:recovery_email",
                        ),
                        confidence=0.40,
                        dedup_key=f"holehe:recovery_email:{service.lower()}:{email}",
                        warnings=[
                            "Partially obfuscated value: a cross-reference hint, "
                            "never a certain identifier."
                        ],
                    )
                )
            recovery_phone = entry.get("phoneNumber")
            if recovery_phone:
                items.append(
                    NormalizedItem(
                        kind=FindingType.OBFUSCATED_CONTACT.value,
                        title=f"{service}: obfuscated recovery phone",
                        payload={
                            "service": service,
                            "recovery_phone_obfuscated": str(recovery_phone).strip(),
                            "certain": False,
                        },
                        source=SourceRef(
                            kind=SourceKind.TOOL_OUTPUT.value,
                            title=f"Holehe - {service}",
                            description="Obfuscated hint provided by the service.",
                            reliability=0.70,
                            raw_reference=f"holehe:{service}:recovery_phone",
                        ),
                        confidence=0.40,
                        dedup_key=f"holehe:recovery_phone:{service.lower()}:{email}",
                        warnings=[
                            "Partially obfuscated value: a cross-reference hint, "
                            "never a certain identifier."
                        ],
                    )
                )

            others = entry.get("others")
            if isinstance(others, dict) and others:
                items.append(
                    NormalizedItem(
                        kind=FindingType.PROFILE_METADATA.value,
                        title=f"{service}: public metadata",
                        payload={"service": service, "data": others},
                        source=SourceRef(
                            kind=SourceKind.TOOL_OUTPUT.value,
                            title=f"Holehe - {service}",
                            reliability=0.60,
                            raw_reference=f"holehe:{service}:others",
                        ),
                        confidence=0.45,
                        dedup_key=f"holehe:others:{service.lower()}:{email}",
                    )
                )
        return items


def _last_json(stdout: str) -> dict | None:
    """Read the last JSON object on stdout (the driver writes nothing else)."""
    text = stdout.strip()
    if not text:
        return None
    for candidate in (text, text.splitlines()[-1]):
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data
    return None


PLUGIN = HolehePlugin
