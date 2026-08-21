"""Sherlock plugin: search for a username across many platforms.

Upstream: https://github.com/sherlock-project/sherlock (MIT)
Installed in the dedicated worker with `pip install sherlock-project`.

IMPORTANT: a Sherlock result is PROOF THAT A USERNAME EXISTS, never proof of
identity. Items produced here therefore carry a `HYPOTHESIS` status and a capped
confidence; only correlation (bio, display name, public email, cross-links) or
an analyst can promote them.
"""
from __future__ import annotations

import csv
import io
import os
import re
import tempfile
from pathlib import Path

from app.core.config import settings
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
from app.plugins.runner import python_executable, run_command, tool_available

#: `[+] Site name: https://...`
FOUND_LINE = re.compile(r"^\[\+\]\s*([^:]+):\s*(\S+)\s*$")


class SherlockPlugin(OSINTPlugin):
    name = "sherlock"
    version = "1.0.0"
    description = (
        "Checks whether a username exists on hundreds of public sites."
    )
    repository = "https://github.com/sherlock-project/sherlock"
    license = "MIT"
    supported_identifiers = [IdentifierType.USERNAME.value, IdentifierType.ALIAS.value]
    queue = "sherlock"
    requests_per_minute = 10
    concurrency = 1
    timeout_seconds = 600
    #: Registered on startup, but never switched on implicitly: enabling a
    #: plugin stays an explicit decision (installer, CLI or UI).
    enabled_by_default = False
    risk_notes = [
        "Generates a large number of outbound requests: keep concurrency low.",
        "The same username on several sites does not imply the same person.",
    ]

    # ----------------------------------------------------------------- health

    def check_health(self) -> HealthStatus:
        argv = self._base_argv()
        if argv is None:
            return HealthStatus(
                ok=False,
                message=(
                    "Sherlock not found. Install it in the dedicated worker: "
                    "pip install sherlock-project"
                ),
            )
        result = run_command(argv + ["--version"], timeout=60)
        output = (result.stdout + result.stderr).strip()
        if result.timed_out:
            return HealthStatus(ok=False, message="health check timed out")
        version = output.splitlines()[0] if output else None
        # Some versions exit non-zero on --version while still printing the
        # version: rely on usable output rather than the exit code.
        return HealthStatus(ok=bool(version), message=output or "no output", version=version)

    # --------------------------------------------------------------- execution

    def execute(self, target: Target) -> RawResult:
        raw = RawResult()
        username = target.value.strip().lstrip("@")
        if not username:
            raw.error = "empty username"
            return raw

        argv = self._base_argv()
        if argv is None:
            raw.error = "sherlock is not installed in this worker"
            return raw

        outdir = tempfile.mkdtemp(prefix="sherlock-")
        argv += [
            username,
            "--print-found",
            "--no-color",
            "--timeout",
            str(int(target.context.get("site_timeout", 20))),
            "--folderoutput",
            outdir,
            "--csv",
        ]

        sites = target.context.get("sites") or []
        for site in sites:
            argv += ["--site", str(site)]
        if settings.osint_https_proxy:
            argv += ["--proxy", settings.osint_https_proxy]

        raw.logs.append(f"[INFO] Starting Sherlock for username: {username}")
        if sites:
            raw.logs.append(f"[INFO] Restricted to {len(sites)} site(s)")

        result = run_command(
            argv, timeout=target.context.get("timeout") or self.timeout_seconds
        )
        raw.meta["returncode"] = result.returncode
        raw.meta["timed_out"] = result.timed_out

        items = self._parse_csv(outdir, username)
        source = "csv"
        if not items:
            items = self._parse_stdout(result.stdout)
            source = "stdout"
        raw.meta["parser"] = source

        for line in result.stdout.splitlines():
            if line.startswith("[*]") or line.startswith("[!]"):
                raw.logs.append(line.strip())

        raw.items = items
        raw.logs.append(f"[INFO] {len(items)} profile(s) found")
        if result.stderr.strip():
            raw.logs.append(f"[STDERR] {result.stderr.strip()[-500:]}")

        if not items and result.timed_out:
            raw.error = "Sherlock timed out (no usable result)"
        elif result.returncode != 0:
            # Sherlock exits 0 even when it finds nothing, so any non-zero
            # code is a real failure. Treating 1 as "no results" used to hide
            # crashes behind a successful-looking run with zero findings.
            raw.error = (
                f"Sherlock exited with code {result.returncode}: "
                f"{result.stderr.strip()[-500:] or 'no error output'}"
            )
        return raw

    # --------------------------------------------------------------- normalise

    def normalize(self, raw: RawResult, target: Target) -> list[NormalizedItem]:
        items: list[NormalizedItem] = []
        for entry in raw.items:
            platform = (entry.get("platform") or "").strip()
            url = (entry.get("url") or "").strip()
            if not platform or not url:
                continue
            username = entry.get("username") or target.value
            items.append(
                NormalizedItem(
                    kind=FindingType.SOCIAL_PROFILE.value,
                    title=f"{platform}: {username}",
                    payload={
                        "type": "social_profile",
                        "platform": platform,
                        "username": username,
                        "url": url,
                        # `claimed` = the username exists on the site, nothing more.
                        "status": "claimed",
                        "http_status": entry.get("http_status"),
                        "verification_status": "HYPOTHESIS",
                        "identity_proven": False,
                    },
                    source=SourceRef(
                        kind=SourceKind.TOOL_OUTPUT.value,
                        url=url,
                        title=f"Sherlock - {platform}",
                        description=(
                            "Username presence detected by Sherlock. This does not "
                            "establish who owns the account."
                        ),
                        reliability=0.70,
                        raw_reference=f"sherlock:{platform}",
                    ),
                    confidence=0.35,
                    dedup_key=f"social:{platform.lower()}:{username.lower()}",
                    derived_identifiers=[
                        {
                            "type": IdentifierType.SOCIAL_PROFILE.value,
                            "value": url,
                            "platform": platform,
                            "confidence": 0.35,
                            "status": "HYPOTHESIS",
                        }
                    ],
                    warnings=[
                        "Username presence, not proof of identity.",
                    ],
                )
            )
        return items

    def validate(self, items: list[NormalizedItem], target: Target) -> list[NormalizedItem]:
        """Cap confidence: Sherlock alone never exceeds 0.40."""
        validated = super().validate(items, target)
        for item in validated:
            item.confidence = min(item.confidence, 0.40)
        return validated

    # -------------------------------------------------------------- internals

    def _base_argv(self) -> list[str] | None:
        if tool_available("sherlock"):
            return ["sherlock"]
        for module in ("sherlock_project", "sherlock"):
            probe = run_command([python_executable(), "-c", f"import {module}"], timeout=30)
            if probe.ok:
                return [python_executable(), "-m", module]
        return None

    def _parse_stdout(self, stdout: str) -> list[dict]:
        items: list[dict] = []
        for line in stdout.splitlines():
            match = FOUND_LINE.match(line.strip())
            if match:
                items.append(
                    {"platform": match.group(1).strip(), "url": match.group(2).strip()}
                )
        return items

    def _parse_csv(self, outdir: str, username: str) -> list[dict]:
        folder = Path(outdir)
        if not folder.exists():
            return []
        items: list[dict] = []
        for csv_file in folder.rglob("*.csv"):
            try:
                content = csv_file.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for row in csv.DictReader(io.StringIO(content)):
                exists = (row.get("exists") or "").strip().lower()
                if exists not in {"claimed", "yes", "true"}:
                    continue
                items.append(
                    {
                        "platform": (row.get("name") or "").strip(),
                        "url": (row.get("url_user") or "").strip(),
                        "username": (row.get("username") or username).strip(),
                        "http_status": row.get("http_status"),
                    }
                )
        # The temporary folder only exists to collect the CSV.
        for path in folder.rglob("*"):
            if path.is_file():
                try:
                    os.unlink(path)
                except OSError:
                    pass
        return items


PLUGIN = SherlockPlugin
