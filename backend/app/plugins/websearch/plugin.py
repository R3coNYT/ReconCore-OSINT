"""Web search plugin: targeted query generation, optional execution.

By default (`SEARCH_PROVIDER=none`) the plugin RUNS NO query at all: it produces
ready-to-use searches the analyst opens themselves. That avoids hammering search
engines and leaves the decision with a human.

To actually run the searches, configure a provider in `.env`:
  * `SEARCH_PROVIDER=searxng` + `SEARXNG_BASE_URL=https://my-instance/`
    (self-hosted instance with the JSON format enabled);
  * `SEARCH_PROVIDER=serpapi` + `SERPAPI_API_KEY=...` (https://serpapi.com);
  * `SEARCH_PROVIDER=brave`   + `BRAVE_SEARCH_API_KEY=...`
    (https://api-dashboard.search.brave.com).
These are official, paid APIs: no result-page scraping, no anti-bot bypass.
"""
from __future__ import annotations

from urllib.parse import quote_plus

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

SOCIAL_SITES = [
    ("instagram.com", "Instagram"),
    ("x.com", "X"),
    ("tiktok.com", "TikTok"),
    ("facebook.com", "Facebook"),
    ("linkedin.com", "LinkedIn"),
    ("github.com", "GitHub"),
    ("reddit.com", "Reddit"),
    ("youtube.com", "YouTube"),
]


class WebSearchPlugin(OSINTPlugin):
    name = "websearch"
    version = "1.0.0"
    description = (
        "Generates targeted search queries and, when an official provider is "
        "configured, runs them and stores the results with their source."
    )
    repository = "interne"
    license = "AGPL-3.0 (ReconCore)"
    supported_identifiers = [
        IdentifierType.NAME.value,
        IdentifierType.USERNAME.value,
        IdentifierType.EMAIL.value,
        IdentifierType.PHONE.value,
        IdentifierType.DOMAIN.value,
        IdentifierType.COMPANY.value,
        IdentifierType.ORGANIZATION.value,
    ]
    queue = "websearch"
    requests_per_minute = 20
    concurrency = 2
    timeout_seconds = 120
    #: Registered on startup, but never switched on implicitly: enabling a
    #: plugin stays an explicit decision (installer, CLI or UI).
    enabled_by_default = False
    risk_notes = [
        "With no provider configured, no request is sent.",
        "Uses official APIs only: no scraping, no circumvention.",
    ]

    def check_health(self) -> HealthStatus:
        provider = settings.search_provider.lower()
        if provider == "none":
            return HealthStatus(
                ok=True,
                message="Query-generation mode (nothing is executed).",
                details={"provider": "none"},
            )
        missing = {
            "searxng": not settings.searxng_base_url,
            "serpapi": not settings.serpapi_api_key,
            "brave": not settings.brave_search_api_key,
        }.get(provider, True)
        if missing:
            return HealthStatus(
                ok=False,
                message=f"SEARCH_PROVIDER={provider} but the matching key or URL is missing.",
                details={"provider": provider},
            )
        return HealthStatus(ok=True, message=f"provider {provider} configured",
                            details={"provider": provider})

    def execute(self, target: Target) -> RawResult:
        raw = RawResult()
        queries = build_queries(target.type, target.value)
        raw.items.append({"kind": "queries", "data": queries})
        raw.logs.append(f"[INFO] {len(queries)} search queries generated")

        provider = settings.search_provider.lower()
        raw.meta["provider"] = provider
        if provider == "none":
            raw.logs.append("[INFO] SEARCH_PROVIDER=none: no query executed")
            return raw

        max_queries = int(target.context.get("max_queries", 6))
        for query in queries[:max_queries]:
            try:
                results = _search(provider, query["query"])
            except httpx.HTTPError as exc:
                raw.logs.append(f"[WARN] {query['query']}: {exc}")
                continue
            raw.items.append(
                {"kind": "results", "query": query["query"], "data": results}
            )
            raw.logs.append(f"[INFO] {len(results)} result(s) for {query['query']}")
        return raw

    def normalize(self, raw: RawResult, target: Target) -> list[NormalizedItem]:
        items: list[NormalizedItem] = []
        for entry in raw.items:
            if entry["kind"] == "queries":
                for query in entry["data"]:
                    items.append(
                        NormalizedItem(
                            kind=FindingType.SEARCH_QUERY.value,
                            title=f"Suggested search: {query['label']}",
                            payload={**query, "executed": False},
                            source=SourceRef(
                                kind=SourceKind.USER_HYPOTHESIS.value,
                                url=query["url"],
                                title=query["label"],
                                description="Generated query, not executed.",
                                reliability=0.20,
                                raw_reference="websearch:query",
                            ),
                            confidence=0.10,
                            dedup_key=f"dork:{target.normalized}:{query['query']}",
                        )
                    )
            elif entry["kind"] == "results":
                for result in entry["data"]:
                    url = result.get("url")
                    if not url:
                        continue
                    items.append(
                        NormalizedItem(
                            kind=FindingType.WEB_RESULT.value,
                            title=result.get("title") or url,
                            payload={
                                "url": url,
                                "title": result.get("title"),
                                "snippet": result.get("snippet"),
                                "query": entry.get("query"),
                                "engine": raw.meta.get("provider"),
                            },
                            source=SourceRef(
                                kind=SourceKind.SEARCH_ENGINE.value,
                                url=url,
                                title=result.get("title"),
                                description=result.get("snippet"),
                                reliability=0.65,
                                raw_reference="websearch:result",
                            ),
                            confidence=0.30,
                            dedup_key=f"web:{url}",
                        )
                    )
        return items


def build_queries(identifier_type: str, value: str) -> list[dict]:
    """Build the queries that suit a given identifier type."""
    value = value.strip()
    quoted = f'"{value}"'
    queries: list[dict] = []

    def add(label: str, query: str, category: str) -> None:
        queries.append(
            {
                "label": label,
                "query": query,
                "category": category,
                "url": f"https://www.google.com/search?q={quote_plus(query)}",
            }
        )

    add("Exact query", quoted, "general")

    if identifier_type in {IdentifierType.USERNAME.value, IdentifierType.ALIAS.value}:
        for site, label in SOCIAL_SITES:
            add(f"{label}", f"site:{site} {quoted}", "social")
        add("Forums and pastes", f'{quoted} (inurl:forum OR inurl:profile OR site:pastebin.com)', "forum")
    elif identifier_type == IdentifierType.EMAIL.value:
        local = value.split("@", 1)[0]
        add("Full address", quoted, "contact")
        add("Local part as a username", f'"{local}"', "username")
        add("Leaks and public mentions", f'{quoted} (site:pastebin.com OR site:github.com)', "leak")
    elif identifier_type == IdentifierType.PHONE.value:
        add("Number", quoted, "contact")
        add("Directories", f"{quoted} (site:pagesjaunes.fr OR site:118712.fr)", "directory")
    elif identifier_type in {IdentifierType.NAME.value}:
        add("Name and social networks", f"{quoted} (linkedin OR facebook OR instagram)", "social")
        add("Name in the press", f"{quoted} (interview OR press release OR article)", "press")
        add("Documents", f"{quoted} (filetype:pdf OR filetype:docx)", "documents")
    elif identifier_type in {IdentifierType.DOMAIN.value}:
        add("Indexed pages", f"site:{value}", "domain")
        add("Subdomains", f"site:*.{value}", "domain")
        add("Mentions of the domain", f'{quoted} -site:{value}', "domain")
    elif identifier_type in {
        IdentifierType.COMPANY.value,
        IdentifierType.ORGANIZATION.value,
    }:
        add("Registries and legal notices", f'{quoted} (company number OR VAT OR "legal notice")', "corporate")
        add("Publicly listed employees", f'{quoted} site:linkedin.com', "corporate")

    return queries


def _search(provider: str, query: str) -> list[dict]:
    """Call the official API of the configured provider."""
    timeout = 30
    if provider == "searxng":
        response = httpx.get(
            f"{settings.searxng_base_url.rstrip('/')}/search",
            params={"q": query, "format": "json"},
            timeout=timeout,
        )
        response.raise_for_status()
        return [
            {
                "url": item.get("url"),
                "title": item.get("title"),
                "snippet": item.get("content"),
            }
            for item in response.json().get("results", [])[:20]
        ]

    if provider == "serpapi":
        response = httpx.get(
            "https://serpapi.com/search.json",
            params={"q": query, "api_key": settings.serpapi_api_key, "num": 20},
            timeout=timeout,
        )
        response.raise_for_status()
        return [
            {
                "url": item.get("link"),
                "title": item.get("title"),
                "snippet": item.get("snippet"),
            }
            for item in response.json().get("organic_results", [])
        ]

    if provider == "brave":
        response = httpx.get(
            "https://api.search.brave.com/res/v1/web/search",
            params={"q": query, "count": 20},
            headers={
                "Accept": "application/json",
                "X-Subscription-Token": settings.brave_search_api_key,
            },
            timeout=timeout,
        )
        response.raise_for_status()
        return [
            {
                "url": item.get("url"),
                "title": item.get("title"),
                "snippet": item.get("description"),
            }
            for item in response.json().get("web", {}).get("results", [])
        ]

    return []


PLUGIN = WebSearchPlugin
