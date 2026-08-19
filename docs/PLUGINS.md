# OSINT plugins

## 1. The contract

Every plugin subclasses `OSINTPlugin` (`backend/app/plugins/base.py`):

```python
class OSINTPlugin:
    name: str
    version: str
    description: str
    repository: str
    license: str
    supported_identifiers: list[str]
    requires_secrets: list[str]
    queue: str                 # Celery queue = dedicated container
    enabled_by_default: bool
    risk_notes: list[str]

    def check_health(self) -> HealthStatus: ...
    def execute(self, target: Target) -> RawResult: ...
    def normalize(self, raw: RawResult, target: Target) -> list[NormalizedItem]: ...
    def validate(self, items, target) -> list[NormalizedItem]: ...
```

`execute` returns **raw** output. `normalize` translates it into the internal
format. `validate` applies guardrails (confidence caps, filtering). A plugin
**never writes to the database**: it returns data, the orchestrator persists it.

Every normalised item carries a mandatory `SourceRef` (category, URL, title,
reliability, raw reference): information without provenance does not enter the
system.

## 2. Checklist before integrating a third-party tool

No plugin is ever enabled implicitly: `enabled_by_default` is `False` for all
of them, so activation is always an explicit decision (installer, `osint plugin
enable`, or the UI). No tool is enabled before clearing these steps:

1. Identify the **official repository** (watch out for same-name forks).
2. Check the **licence** and its compatibility with your use.
3. Check project **activity** (recent commits, open issues).
4. Read `requirements.txt` / `pyproject.toml` / `setup.py`.
5. Inspect **Dockerfiles** and **shell scripts**.
6. Search for `subprocess`, `os.system`, `eval`, `exec`, `pickle`.
7. Search for **dynamic downloads** at runtime.
8. Search for **hardcoded secrets**.
9. Inspect **GitHub Actions** (a classic exfiltration path via CI).
10. Check **releases** and their **checksums** where they exist.
11. Inspect **transitive dependencies**.
12. Run it in a **sandbox** first (dedicated worker, no DB access).
13. Assign a **risk level** and record it in `manifest.json`.
14. Only then: enable it.

Tooling:

```bash
osint plugin audit <plugin>
```

```bash
osint plugin audit all --json
```

The report follows the expected format:

```text
Plugin Security Report

Repository:          https://github.com/sherlock-project/sherlock
License:             MIT
Version:             0.15.0
Last update:         2025-06-01

Risk level:          LOW

Network access:      YES
Filesystem access:   LIMITED
Subprocess:          YES
Dynamic downloads:   NO
Privileged ops:      NO
Docker socket:       NO
Hardcoded secrets:   NO
Suspicious behavior: NONE DETECTED
```

The exit code is `2` when the level is `HIGH` or `CRITICAL` — usable in CI.

> **This analysis is an aid, not a guarantee.** It detects known patterns in
> readable code. It says nothing about a binary, a compromised transitive
> dependency, or behaviour triggered remotely.

### Auditing upstream code too

By default the audit covers the integration code (the plugin directory). To
analyse the third-party sources as well, clone upstream and set `vendor_path` in
`manifest.json`:

```json
{ "vendor_path": "../../../vendor/sherlock" }
```

If `vendor_path` is declared but missing, the report says so explicitly rather
than implying upstream was analysed.

## 3. Bundled plugins

### sherlock — username search

- Upstream: <https://github.com/sherlock-project/sherlock> (MIT)
- Install: `pip install sherlock-project` (done in `worker-sherlock`)
- Input: `USERNAME`, `ALIAS`
- Output: detected profiles (platform, URL, HTTP status)

Enforced behaviour: confidence **capped at 0.40**, `HYPOTHESIS` status,
`identity_proven: false`, and a warning attached to every item. Sherlock proves a
username exists on a site — nothing more.

Parsing reads the generated CSV first, falling back to stdout
(`[+] Site: url`): both formats are stable across versions.

### holehe — services tied to an email address

- Upstream: <https://github.com/megadose/holehe> (GPL-3.0)
- Install: `pip install holehe` (done in `worker-holehe`)
- Input: `EMAIL`

Three distinct states, never conflated:

| Upstream result         | Stored as                                   |
|-------------------------|---------------------------------------------|
| `exists: true`          | `account_exists` / `result: used`           |
| `rateLimit: true`       | `account_exists` / `result: inconclusive`   |
| `exists: false`         | nothing (an absence is not a discovery)     |

Obfuscated values (`emailrecovery`, `phoneNumber`) become
`recovery_email_obfuscated` / `recovery_phone_obfuscated` with `certain: false`.
No reconstruction is attempted.

Holehe runs as a **subprocess** (`app/plugins/holehe/driver.py`): its dependency
pins stay confined to its own container, and the GPL code is not linked into the
rest of the platform.

### phoneinfoga — phone number reconnaissance

- Upstream: <https://github.com/sundowndev/phoneinfoga> (GPL-3.0)
- Input: `PHONE`

Three modes, selected automatically:

1. **REST** — `PHONEINFOGA_API_URL` is set (the stack's official container).
   Scanners called: `local`, `googlesearch`.
2. **CLI** — a `phoneinfoga` binary is present in the worker.
3. **Local** — always available: validation and metadata via `phonenumbers`
   (country, carrier, area, line type, timezones) plus search-query generation.
   **No outbound request.**

Keys for external scanners (numverify, OVH) are configured **on the PhoneInfoga
container**, never inside ReconCore.

Verifying a manually downloaded release:

```bash
sha256sum -c phoneinfoga_checksums.txt
```

### websearch — targeted queries

- Input: name, username, email, phone, domain, company
- By default (`SEARCH_PROVIDER=none`) it **generates** queries without running
  them. The analyst opens whichever ones are relevant.

To actually run searches, configure an official provider:

| `SEARCH_PROVIDER` | Variable                | Where to get the key                               |
|-------------------|-------------------------|-----------------------------------------------------|
| `searxng`         | `SEARXNG_BASE_URL`      | your self-hosted instance (JSON format enabled)     |
| `serpapi`         | `SERPAPI_API_KEY`       | <https://serpapi.com>                               |
| `brave`           | `BRAVE_SEARCH_API_KEY`  | <https://api-dashboard.search.brave.com>            |

Official APIs only: no result-page scraping, no anti-bot circumvention.

### toutatis — Instagram (optional, disabled by default)

- Upstream: <https://github.com/megadose/toutatis> (GPL-3.0)
- Input: `USERNAME` (only when an Instagram profile is already linked to the
  person, or when `instagram: true` is passed explicitly)
- Required secret: `sessionid`

**Non-negotiable rules:**

- ReconCore never asks for your Instagram password and never stores one.
- No login automation, no authentication or anti-abuse circumvention.
- The cookie is supplied manually, encrypted at rest, and passed to the
  subprocess **over STDIN** (never as a command-line argument, where it would be
  visible in the process list, and never as an environment variable).
- The driver masks the cookie in any output it returns.
- The plugin can be switched off entirely with `TOUTATIS_ENABLED=false`, in
  which case no upstream code is called at all.

**Risks to accept before enabling:** using an account to collect data may breach
Instagram's terms of service and get that account suspended. Use a dedicated
account, and log it out (which revokes the cookie) when you no longer need it.

Enabling:

```bash
# 1. in .env
TOUTATIS_ENABLED=true
```

```bash
docker compose --profile toutatis up -d worker-toutatis
```

```bash
docker compose exec -it api python -m app.cli plugin secret set toutatis sessionid
```

```bash
docker compose exec api python -m app.cli plugin enable toutatis --acknowledge-risks
```

Where to find the cookie: in **your own** browser logged into Instagram, developer
tools > Application > Cookies > `instagram.com` > `sessionid`.

Revoking: logging out of Instagram invalidates the cookie; then delete the
stored secret:

```bash
docker compose exec api python -m app.cli plugin secret delete toutatis sessionid
```

## 4. Quotas and respecting services

Each plugin declares `requests_per_minute`, `concurrency`, `timeout_seconds` and
`retry_count`, all adjustable by an administrator
(`PATCH /plugins/{name}/limits`). `PluginRateLimiter` makes **our own tasks
wait** when the quota is reached: a deliberate brake, never a bypass.

Conservative defaults:

| Plugin        | req/min | concurrency | timeout |
|---------------|---------|-------------|---------|
| sherlock      | 10      | 1           | 600 s   |
| holehe        | 6       | 1           | 420 s   |
| phoneinfoga   | 20      | 2           | 180 s   |
| websearch     | 20      | 2           | 120 s   |
| toutatis      | 4       | 1           | 240 s   |

## 5. Writing a new plugin

```python
# backend/app/plugins/mytool/plugin.py
from app.plugins.base import (
    FindingType, HealthStatus, IdentifierType, NormalizedItem,
    OSINTPlugin, RawResult, SourceKind, SourceRef, Target,
)
from app.plugins.runner import run_command, tool_available


class MyToolPlugin(OSINTPlugin):
    name = "mytool"
    version = "1.0.0"
    description = "What the tool does, in one sentence."
    repository = "https://github.com/..."
    license = "MIT"
    supported_identifiers = [IdentifierType.USERNAME.value]
    queue = "mytool"
    requests_per_minute = 10
    enabled_by_default = False

    def check_health(self) -> HealthStatus:
        if not tool_available("mytool"):
            return HealthStatus(ok=False, message="binary missing: pip install mytool")
        return HealthStatus(ok=True, message="available")

    def execute(self, target: Target) -> RawResult:
        raw = RawResult()
        result = run_command(["mytool", "--json", target.normalized],
                             timeout=self.timeout_seconds)
        if result.timed_out:
            raw.error = "timeout"
            return raw
        raw.items = parse(result.stdout)      # write `parse` yourself
        return raw

    def normalize(self, raw: RawResult, target: Target) -> list[NormalizedItem]:
        return [
            NormalizedItem(
                kind=FindingType.WEB_RESULT.value,
                title=item["title"],
                payload=item,
                source=SourceRef(
                    kind=SourceKind.TOOL_OUTPUT.value,
                    url=item.get("url"),
                    reliability=0.6,
                    raw_reference=f"mytool:{item['id']}",
                ),
                confidence=0.4,
                dedup_key=f"mytool:{item['id']}",
            )
            for item in raw.items
        ]


PLUGIN = MyToolPlugin
```

Then add `manifest.json` (provenance, licence, execution mode, review notes), a
`docker/worker-mytool.Dockerfile`, the matching service in `docker-compose.yml`
(with the `*tool-hardening` block), and finally:

```bash
osint plugin audit mytool
```

```bash
osint plugin enable mytool
```

## 6. Where to look for new tools

<https://github.com/topics/osint-tools> is a discovery source, **not** a trust
list. No tool is integrated merely because it appears there: the checklist in
section 2 applies in full, every time.
