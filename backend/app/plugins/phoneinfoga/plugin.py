"""PhoneInfoga plugin: reconnaissance on a phone number.

Upstream: https://github.com/sundowndev/phoneinfoga (GPL-3.0)

Three modes, selected automatically in this order:
  1. PhoneInfoga REST API (`PHONEINFOGA_API_URL`) - dedicated container,
     recommended;
  2. `phoneinfoga` binary present in the worker (subprocess);
  3. built-in local mode: validation/metadata through `phonenumbers` plus
     search-query generation (dorks). Always available, no dependency.

Mode 3 contacts no service at all: it produces QUERIES to run, which leaves the
analyst in control of what actually gets searched.
"""
from __future__ import annotations

import json

import httpx

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
from app.plugins.runner import run_command, tool_available
from app.services.normalization import normalize_phone, phone_metadata

#: REST scanners queried when the PhoneInfoga API is available.
REST_SCANNERS = ("local", "googlesearch")


class PhoneInfogaPlugin(OSINTPlugin):
    name = "phoneinfoga"
    version = "1.0.0"
    description = (
        "Phone number reconnaissance: validity, carrier, area, and generation of "
        "targeted public searches."
    )
    repository = "https://github.com/sundowndev/phoneinfoga"
    license = "GPL-3.0"
    supported_identifiers = [IdentifierType.PHONE.value]
    queue = "phoneinfoga"
    requests_per_minute = 20
    concurrency = 2
    timeout_seconds = 180
    #: Registered on startup, but never switched on implicitly: enabling a
    #: plugin stays an explicit decision (installer, CLI or UI).
    enabled_by_default = False
    risk_notes = [
        "External scanners (numverify, ovh) need keys configured on the "
        "PhoneInfoga container itself.",
        "Local mode issues no request: it only proposes searches to run.",
    ]

    # ------------------------------------------------------------------ health

    def check_health(self) -> HealthStatus:
        if settings.phoneinfoga_api_url:
            try:
                response = httpx.get(
                    f"{settings.phoneinfoga_api_url.rstrip('/')}/api/v2/scanners",
                    timeout=10,
                )
                ok = response.status_code == 200
                scanners = []
                if ok:
                    scanners = [
                        item.get("name")
                        for item in response.json().get("scanners", [])
                    ]
                return HealthStatus(
                    ok=ok,
                    message=(
                        f"PhoneInfoga API reachable, scanners: {', '.join(scanners)}"
                        if ok
                        else f"PhoneInfoga API returned HTTP {response.status_code}"
                    ),
                    details={"mode": "rest", "scanners": scanners},
                )
            except httpx.HTTPError as exc:
                return HealthStatus(
                    ok=False,
                    message=f"PhoneInfoga API unreachable: {exc}",
                    details={"mode": "rest"},
                )
        if tool_available("phoneinfoga"):
            result = run_command(["phoneinfoga", "version"], timeout=30)
            return HealthStatus(
                ok=result.ok,
                message=result.stdout.strip() or result.stderr.strip() or "binary present",
                details={"mode": "cli"},
            )
        return HealthStatus(
            ok=True,
            message=(
                "Local mode (phonenumbers + dork generation). To enable full "
                "PhoneInfoga, set PHONEINFOGA_API_URL or install the binary."
            ),
            details={"mode": "local"},
        )

    # --------------------------------------------------------------- execution

    def execute(self, target: Target) -> RawResult:
        raw = RawResult()
        number = normalize_phone(target.value, target.context.get("region", "FR"))
        if not number:
            raw.error = "empty phone number"
            return raw
        raw.logs.append(f"[INFO] Starting PhoneInfoga for {number}")

        metadata = phone_metadata(number)
        raw.items.append({"kind": "local_metadata", "data": metadata})
        raw.meta["number"] = number

        if not metadata.get("possible"):
            raw.logs.append("[WARN] Number reported as implausible by the validation library")

        if settings.phoneinfoga_api_url:
            raw.meta["mode"] = "rest"
            self._run_rest(number, raw)
        elif tool_available("phoneinfoga"):
            raw.meta["mode"] = "cli"
            self._run_cli(number, raw)
        else:
            raw.meta["mode"] = "local"
            raw.logs.append("[INFO] External PhoneInfoga absent: local mode")

        dorks = _build_dorks(number, metadata)
        raw.items.append({"kind": "dorks", "data": dorks})
        raw.logs.append(f"[INFO] {len(dorks)} search queries generated")
        return raw

    def _run_rest(self, number: str, raw: RawResult) -> None:
        base = settings.phoneinfoga_api_url.rstrip("/")
        # The REST API expects digits only: it rejects "+" and separators.
        api_number = "".join(ch for ch in number if ch.isdigit())
        for scanner in REST_SCANNERS:
            try:
                response = httpx.post(
                    f"{base}/api/v2/scanners/{scanner}/run",
                    json={"number": api_number},
                    timeout=self.timeout_seconds,
                )
                if response.status_code >= 400:
                    raw.logs.append(
                        f"[WARN] scanner {scanner}: HTTP {response.status_code} "
                        f"{response.text[:200]}"
                    )
                    continue
                raw.items.append(
                    {"kind": "scanner", "scanner": scanner, "data": response.json()}
                )
                raw.logs.append(f"[INFO] scanner {scanner}: ok")
            except (httpx.HTTPError, json.JSONDecodeError) as exc:
                raw.logs.append(f"[WARN] scanner {scanner}: {exc}")

    def _run_cli(self, number: str, raw: RawResult) -> None:
        result = run_command(
            ["phoneinfoga", "scan", "-n", number], timeout=self.timeout_seconds
        )
        if result.timed_out:
            raw.logs.append("[WARN] phoneinfoga binary timed out")
            return
        if result.stdout.strip():
            raw.items.append({"kind": "cli_output", "data": result.stdout[:20000]})
        if result.stderr.strip():
            raw.logs.append(f"[WARN] {result.stderr.strip()[:500]}")

    # --------------------------------------------------------------- normalise

    def normalize(self, raw: RawResult, target: Target) -> list[NormalizedItem]:
        number = raw.meta.get("number") or target.normalized
        items: list[NormalizedItem] = []

        for entry in raw.items:
            kind = entry.get("kind")

            if kind == "local_metadata":
                data = entry["data"]
                items.append(
                    NormalizedItem(
                        kind=FindingType.PHONE_INFO.value,
                        title=(
                            f"Number {data.get('international') or number} - "
                            f"{data.get('location') or 'unknown area'}"
                        ),
                        payload={
                            "number": number,
                            "valid": data.get("valid"),
                            "type": data.get("type"),
                            "country": data.get("region"),
                            "country_code": data.get("country_code"),
                            "location": data.get("location"),
                            "carrier": data.get("carrier"),
                            "timezones": data.get("timezones"),
                            "formats": {
                                "e164": data.get("e164"),
                                "international": data.get("international"),
                                "national": data.get("national"),
                            },
                        },
                        source=SourceRef(
                            kind=SourceKind.ESTABLISHED_DATABASE.value,
                            title="Numbering plan metadata (libphonenumber)",
                            description="Public numbering plan, not a directory.",
                            reliability=0.85,
                            raw_reference="phoneinfoga:local",
                        ),
                        confidence=0.80 if data.get("valid") else 0.30,
                        dedup_key=f"phone_info:{number}",
                    )
                )

            elif kind == "scanner":
                payload = entry.get("data") or {}
                result = payload.get("result", payload)
                items.append(
                    NormalizedItem(
                        kind=FindingType.PHONE_INFO.value,
                        title=f"PhoneInfoga - {entry.get('scanner')} scanner",
                        payload={"scanner": entry.get("scanner"), "result": result},
                        source=SourceRef(
                            kind=SourceKind.TOOL_OUTPUT.value,
                            title=f"PhoneInfoga {entry.get('scanner')}",
                            reliability=0.70,
                            raw_reference=f"phoneinfoga:{entry.get('scanner')}",
                        ),
                        confidence=0.55,
                        dedup_key=f"phoneinfoga:{entry.get('scanner')}:{number}",
                    )
                )
                items.extend(_urls_from_scanner(result, number, entry.get("scanner")))

            elif kind == "cli_output":
                items.append(
                    NormalizedItem(
                        kind=FindingType.PHONE_INFO.value,
                        title="PhoneInfoga - CLI output",
                        payload={"output": entry["data"]},
                        source=SourceRef(
                            kind=SourceKind.TOOL_OUTPUT.value,
                            title="PhoneInfoga CLI",
                            reliability=0.70,
                            raw_reference="phoneinfoga:cli",
                        ),
                        confidence=0.50,
                        dedup_key=f"phoneinfoga:cli:{number}",
                    )
                )

            elif kind == "dorks":
                for dork in entry["data"]:
                    items.append(
                        NormalizedItem(
                            kind=FindingType.SEARCH_QUERY.value,
                            title=f"Suggested search: {dork['label']}",
                            payload={
                                "query": dork["query"],
                                "url": dork["url"],
                                "engine": dork["engine"],
                                "category": dork["category"],
                                "executed": False,
                            },
                            source=SourceRef(
                                kind=SourceKind.USER_HYPOTHESIS.value,
                                url=dork["url"],
                                title=dork["label"],
                                description="Generated query, not executed.",
                                reliability=0.20,
                                raw_reference="phoneinfoga:dork",
                            ),
                            confidence=0.10,
                            dedup_key=f"dork:{number}:{dork['query']}",
                        )
                    )
        return items


def _urls_from_scanner(result: dict, number: str, scanner: str | None) -> list[NormalizedItem]:
    """Extract URLs proposed by a scanner (googlesearch returns dorks)."""
    items: list[NormalizedItem] = []
    if not isinstance(result, dict):
        return items
    for group_name, group in result.items():
        entries = group if isinstance(group, list) else []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            url = entry.get("url") or entry.get("URL")
            if not url:
                continue
            items.append(
                NormalizedItem(
                    kind=FindingType.SEARCH_QUERY.value,
                    title=f"{group_name}: {entry.get('dork') or url}",
                    payload={
                        "query": entry.get("dork"),
                        "url": url,
                        "engine": "google",
                        "category": str(group_name),
                        "executed": False,
                    },
                    source=SourceRef(
                        kind=SourceKind.USER_HYPOTHESIS.value,
                        url=url,
                        title=f"PhoneInfoga {scanner} - {group_name}",
                        reliability=0.20,
                        raw_reference=f"phoneinfoga:{scanner}:{group_name}",
                    ),
                    confidence=0.10,
                    dedup_key=f"dork:{number}:{url}",
                )
            )
    return items


def _build_dorks(number: str, metadata: dict) -> list[dict]:
    """Build public search queries from the number's various formats."""
    national = (metadata.get("national") or "").strip()
    international = (metadata.get("international") or "").strip()
    variants = {v for v in (number, national, international) if v}
    variants |= {v.replace(" ", "") for v in list(variants)}
    variants |= {v.replace(" ", ".") for v in [national] if national}

    dorks: list[dict] = []

    def add(label: str, query: str, category: str, engine: str = "google") -> None:
        url = {
            "google": f"https://www.google.com/search?q={_quote(query)}",
            "bing": f"https://www.bing.com/search?q={_quote(query)}",
            "duckduckgo": f"https://duckduckgo.com/?q={_quote(query)}",
        }[engine]
        dorks.append(
            {
                "label": label,
                "query": query,
                "url": url,
                "engine": engine,
                "category": category,
            }
        )

    joined = " OR ".join(f'"{v}"' for v in sorted(variants))
    add("All number formats", joined, "general")
    for engine in ("bing", "duckduckgo"):
        add(f"All formats ({engine})", joined, "general", engine)

    for site, label in (
        ("facebook.com", "Facebook"),
        ("instagram.com", "Instagram"),
        ("x.com", "X"),
        ("linkedin.com", "LinkedIn"),
        ("t.me", "Telegram"),
    ):
        add(label, f"site:{site} ({joined})", "social")

    for site, label in (
        ("leboncoin.fr", "Leboncoin"),
        ("pagesjaunes.fr", "Pages Jaunes"),
        ("annuaire-inverse.net", "Reverse directory"),
    ):
        add(label, f"site:{site} ({joined})", "directory")

    add(
        "Public documents",
        f"({joined}) (filetype:pdf OR filetype:xlsx OR filetype:docx)",
        "documents",
    )
    add(
        "Reports / reputation",
        f"({joined}) (scam OR spam OR fraud OR complaint)",
        "reputation",
    )
    return dorks


def _quote(value: str) -> str:
    from urllib.parse import quote_plus

    return quote_plus(value)


PLUGIN = PhoneInfogaPlugin
