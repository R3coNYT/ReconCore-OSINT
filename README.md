# ReconCore OSINT

An OSINT investigation platform: build case files, collect public information
incrementally, correlate it, score it explainably, and render the result as an
identity graph.

Three principles shape every line of this codebase:

1. **No information without provenance.** Every item keeps its source, its
   timestamp and a reliability rating.
2. **No automatic conclusions.** Tools produce *hypotheses*. Only an analyst
   confirms, rejects, or flags something for review.
3. **No third-party tool on the host.** Every external tool runs in its own
   container, with no access to the database, the host, or the Docker socket.

---

## Contents

- [Quick start](#quick-start)
- [Architecture](#architecture)
- [Features](#features)
- [OSINT plugins](#osint-plugins)
- [Configuration](#configuration)
- [Admin CLI](#admin-cli)
- [Development](#development)
- [Tests](#tests)
- [Acceptable use](#acceptable-use)
- [Documentation](#documentation)

---

## Quick start

Requirements: Docker and Docker Compose. Nothing else — no Python, no Node.

**Linux, macOS, WSL, Git Bash**

```bash
curl -fsSL https://raw.githubusercontent.com/R3coNYT/ReconCore-OSINT/main/install.sh | sh
```

**Windows (PowerShell)**

```powershell
irm https://raw.githubusercontent.com/R3coNYT/ReconCore-OSINT/main/install.ps1 | iex
```

From an existing clone, run `./install.sh` (or `.\install.ps1`) instead.

The installer asks for the HTTP port, the administrator email and password, and
which plugins to enable, then does everything else:

1. checks Docker is installed and running;
2. clones the repository if you are not already inside it;
3. generates `SECRET_KEY`, `SECRETS_ENCRYPTION_KEY` and `POSTGRES_PASSWORD`
   into `.env` (system CSPRNG, `chmod 600`);
4. builds the images and starts the stack;
5. waits for the API to become healthy;
6. creates the schema, seeds the platform catalogue, registers the plugins;
7. creates the administrator account and clears the password from `.env`;
8. enables exactly the plugins you selected.

It is idempotent: run it again to update an existing install without touching
your secrets or your data.

### Unattended install

```bash
./install.sh --yes --email admin@example.org --password 'S3cret!Passw0rd' --port 8080 --plugins sherlock,holehe,phoneinfoga,websearch
```

```powershell
.\install.ps1 -Yes -Email admin@example.org -Password 'S3cret!Passw0rd' -Port 8080 -Plugins sherlock,websearch
```

Useful flags: `--plugins none`, `--no-build`, `--dir <path>`, `--repo <url>`.
`--help` lists them all.

The interface is then on <http://localhost:8080> (or the port you chose).

> No plugin is ever enabled implicitly — not even after an update. Toutatis
> additionally requires `TOUTATIS_ENABLED=true` and a session cookie; see
> [OSINT plugins](#osint-plugins).

### Manual install

If you prefer to drive it yourself:

```bash
cp .env.example .env    # then fill in the three required secrets
```

```bash
docker compose build && docker compose up -d
```

```bash
docker compose exec -T api python -m app.cli setup --enable sherlock,websearch
```

`osint setup` is the same command the installer runs: schema, platform
catalogue, plugin registry, administrator account (from `FIRST_ADMIN_EMAIL` /
`FIRST_ADMIN_PASSWORD`, or `--admin-email` / `--admin-password`) and plugin
activation, all idempotent.

---

## Architecture

```text
                          Browser
                             |
                          Nginx (8080)
                     /                 \
              React frontend        FastAPI API
                                         |
                        +----------------+----------------+
                        |                                 |
                   PostgreSQL                      Redis (broker)
                        |                                 |
                  Orchestrator worker  <---- results -----+
                        |                                 |
                        |                    +------------+------------+
                        |                    |            |            |
                        |              worker-        worker-      worker-
                        |              sherlock       holehe    phoneinfoga
                        |                    |            |            |
                        +-- persistence <----+------------+------------+
                             scoring, correlation, graph
```

Network segmentation (docker-compose):

| Network  | Members                                   | Internet |
|----------|-------------------------------------------|----------|
| `data`   | postgres, api, worker, beat               | no       |
| `bus`    | redis, api, worker, tool workers          | no       |
| `edge`   | nginx, api, frontend                      | no       |
| `egress` | tool workers, phoneinfoga                 | yes      |

The direct consequence: **a tool worker cannot reach PostgreSQL.** It receives a
target, returns data, and the orchestrator — the only service with database
access — decides what gets written.

Full details: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

---

## Features

**Investigation case files** — people, organisations, companies, domains,
pseudonyms. Information is added incrementally; a case file grows over time and
can be reopened days later to be enriched further.

**First-class username handling** — a username is an entity in its own right: it
can exist with no known platform, and carries a status (confirmed / probable /
unknown / rejected), a source, a discovery date and a note.

**Username variants** — derived forms are generated (`jdupont`, `j.dupont`,
`jean.dupont`, `dupontjean`, numeric suffixes...). They are stored as
**hypotheses** (`is_variant`, `HYPOTHESIS` status, capped confidence) and are
never automatically attributed to the person.

**Multi-plugin search** — Sherlock (usernames), Holehe (emails), PhoneInfoga
(phone numbers), web search, Toutatis (Instagram, optional).

**Differential search** — a plugin is not re-run against a target it already
processed successfully within the last 7 days. Adding one new username only
triggers the work that concerns that username.

**Controlled depth** — levels 1 to 4. Discovered entities become targets for the
next level, but only if automation is enabled on the case file.

**Correlation and explainable scoring** — every score is a sum of named
contributions, displayed as-is:

```text
Score: 87 %

+25 Matching email
+20 Matching username
+15 Matching name
+12 Consistent bio
+10 Consistent city
 +5 Reliable source
```

An identical username **on its own** deliberately produces a low score, plus a
`single_weak_signal` penalty: the same handle on four platforms may well be four
different people.

**Contradictions** — two different cities? The system records the contradiction,
displays it, and waits for a human decision. It never picks a side.

**Human validation** — every finding and every profile can be confirmed,
rejected or flagged for review. A rejected item stops contributing to scores.

**Identity graph** — Cytoscape.js, filtering by node type and minimum score;
hypotheses are drawn with dashed outlines.

**Duplicate detection and merge** — suggested with its score and full breakdown,
never applied without explicit confirmation.

**Exports** — JSON, CSV, PDF (complete case file: identifiers, usernames,
profiles, findings, sources, relationships, timeline, scores, contradictions,
search history).

**Security** — Argon2id, JWT plus rotating refresh tokens, RBAC (ADMIN /
ANALYST / READ_ONLY), audit log, encrypted secrets, rate limiting, retention
policy with permanent deletion.

---

## OSINT plugins

| Plugin        | Target        | Upstream                                           | Licence | Default state |
|---------------|---------------|----------------------------------------------------|---------|---------------|
| `sherlock`    | username      | <https://github.com/sherlock-project/sherlock>     | MIT     | enable it     |
| `holehe`      | email         | <https://github.com/megadose/holehe>               | GPL-3.0 | enable it     |
| `phoneinfoga` | phone number  | <https://github.com/sundowndev/phoneinfoga>        | GPL-3.0 | enable it     |
| `websearch`   | any           | in-house                                            | AGPL-3.0| enable it     |
| `toutatis`    | Instagram     | <https://github.com/megadose/toutatis>             | GPL-3.0 | **disabled**  |

### What the plugins deliberately do not do

- **Sherlock** proves a username exists on a site. Not who owns it. Its results
  are capped at 0.40 confidence until another signal converges.
- **Holehe** distinguishes three states: account exists, no account, and
  *inconclusive* (service protected or rate-limited). A rate limit is never
  interpreted as the absence of an account.
- **Obfuscated values** (`j***@gmail.com`, `+33*****78`) are stored verbatim as
  cross-reference hints. No attempt is made to reconstruct them.
- **PhoneInfoga** in local mode sends no requests at all: it produces searches
  that the analyst opens themselves.
- **websearch** with no provider configured only generates queries. With a
  provider it uses official APIs — never result-page scraping, never anti-bot
  circumvention.

### Toutatis: optional, disabled by default

Toutatis requires an Instagram `sessionid` cookie. The platform therefore
applies the following rules, without exception:

- **never** asks for or stores a password;
- **no** login automation, no authentication or anti-abuse bypass;
- the cookie is supplied **manually** by an administrator, encrypted at rest
  (Fernet) and passed to the subprocess **over STDIN only**;
- the plugin can be switched off entirely (`TOUTATIS_ENABLED=false`), in which
  case no upstream code is ever called;
- the UI displays the risks (including possible suspension of the account used,
  under Instagram's terms of service) and requires explicit acknowledgement
  before activation.

Step-by-step procedure: [docs/PLUGINS.md](docs/PLUGINS.md).

### Adding a new tool

Every integration follows the same checklist (official repository, licence,
project activity, dependencies, Dockerfiles, shell scripts, system calls,
dynamic downloads, hardcoded secrets, GitHub Actions, sandboxed execution)
before activation. The static audit is tooled:

```bash
docker compose exec api python -m app.cli plugin audit <plugin>
```

It produces a report (risk level, network/filesystem access, subprocess usage,
dynamic downloads, Docker socket, hardcoded secrets, HIGH/CRITICAL signals
located at file:line) and **is never a guarantee**: reading the code and running
it in a sandbox remain mandatory.

---

## Configuration

Everything is configured through environment variables (`.env`). See
`.env.example` for the annotated list. The mandatory values:

| Variable                   | Purpose                                             |
|----------------------------|-----------------------------------------------------|
| `SECRET_KEY`               | JWT signing key (32+ random characters)             |
| `SECRETS_ENCRYPTION_KEY`   | Fernet key used to encrypt plugin secrets           |
| `POSTGRES_PASSWORD`        | Database password                                   |

Optional API keys, and where to obtain them:

| Variable                | Provider                                                             |
|-------------------------|----------------------------------------------------------------------|
| `SERPAPI_API_KEY`       | <https://serpapi.com> (account + key)                                |
| `BRAVE_SEARCH_API_KEY`  | <https://api-dashboard.search.brave.com>                             |
| `SEARXNG_BASE_URL`      | URL of your self-hosted SearXNG instance (JSON format enabled)       |
| `PHONEINFOGA_API_URL`   | Provided by the stack's `phoneinfoga` container (`http://phoneinfoga:5000`) |

The platform works without any of these keys: `websearch` limits itself to
generating queries, and PhoneInfoga runs in local mode.

In production the application **refuses to start** if `SECRET_KEY` is still the
development default or if `SECRETS_ENCRYPTION_KEY` is missing.

---

## Admin CLI

```bash
osint setup --enable sherlock,websearch      # schema + admin + plugins, idempotent
osint db init                                # tables + platforms + registry
osint user create --email a@b.org --role ADMIN
osint user list
osint plugin list
osint plugin audit sherlock                  # security report
osint plugin audit all --json
osint plugin enable sherlock --acknowledge-risks
osint plugin disable toutatis
osint plugin health sherlock                 # run INSIDE the matching worker
osint plugin secret set toutatis sessionid   # masked prompt, encrypted at rest
osint plugin secret list toutatis            # masked preview only
osint retention apply                        # permanent deletion
```

Inside the Docker stack, prefix with `docker compose exec api python -m app.cli`.

---

## Development

Backend (outside Docker, with PostgreSQL and Redis reachable):

```bash
cd backend && python -m venv .venv && .venv/bin/pip install -r requirements-dev.txt && .venv/bin/pip install -e .
```

```bash
.venv/bin/python -m app.cli db init
```

```bash
.venv/bin/uvicorn app.main:app --reload
```

Worker (all queues locally):

```bash
cd backend && .venv/bin/celery -A app.workers.celery_app.celery_app worker --queues=default,sherlock,holehe,phoneinfoga,websearch --concurrency=2 --loglevel=INFO
```

Frontend:

```bash
cd frontend && npm install && npm run dev
```

Interactive API documentation: <http://localhost:8000/docs>
(automatically disabled in production).

---

## Tests

```bash
cd backend && .venv/bin/python -m pytest
```

100 tests, no infrastructure required: the models use portable column types
(`GUID`, `JSONDict`, `TZDateTime`), so the integration suite runs on SQLite. To
replay it against PostgreSQL:

```bash
RECONCORE_TEST_DATABASE_URL=postgresql+psycopg://user:pass@localhost/reconcore_test python -m pytest
```

Coverage: normalisation (phone numbers, emails, usernames), explainable scoring
and its guardrails, hypothetical variants, the plugin contract and the
normalisation of plugin output (including "inconclusive" results and obfuscated
values), the static security audit, a full API walkthrough (auth, RBAC,
contradictions, ingestion, human validation, duplicates, graph, exports,
encrypted secrets, differential search) and infrastructure invariants (no tool
worker on the database network, no Docker socket, no privileged container).

```bash
cd frontend && npm run lint && npm run build
```

---

## Acceptable use

This tool collects and cross-references personal data from public sources. Using
it carries responsibility:

- use it only within a **lawful, documented** framework (the case file's "legal
  basis" field exists for exactly this);
- respect the terms of service of the platforms you query;
- respect rate limits and do not circumvent anti-abuse protections — the
  platform implements no such circumvention and will not accept any;
- apply a retention policy (`DATA_RETENTION_DAYS`) and delete case files that no
  longer have a reason to exist;
- remember that a high score is still a hypothesis: misattributing an identity
  has real consequences for the person concerned.

---

## Documentation

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — data model, flows, orchestration
- [docs/SECURITY.md](docs/SECURITY.md) — threat model, isolation, secrets, GDPR
- [docs/PLUGINS.md](docs/PLUGINS.md) — integrating, auditing and configuring plugins
