# Architecture

## 1. Overview

ReconCore keeps three responsibilities strictly separated:

| Layer            | Role                                                          | DB access |
|------------------|---------------------------------------------------------------|-----------|
| API (FastAPI)    | CRUD, RBAC, audit, launching searches                          | yes       |
| Orchestrator     | planning, persistence, correlation, scoring, retention         | yes       |
| Tool workers     | running third-party tools, returning raw data                  | **no**    |

A plugin never touches the database. It receives a target, returns normalised
items, and the orchestrator decides what gets written. Third-party tool output
therefore cannot write into the system directly, even if the tool is
compromised.

## 2. Search flow

```text
POST /api/v1/searches
        |
        v
   Search (PENDING)  --- commit ---> Celery task `start_search` (default queue)
        |
        v
   plan_search()  : enabled compatible plugins, minus already-processed targets
        |
        v
   for each step:
        PluginRun (RUNNING)
        chain(
          plugin_execute  --> dedicated queue (worker-sherlock, worker-holehe, ...)
          persist_plugin_result --> default queue (has DB access)
        )
        |
        v
   ingest_items() : Source -> Finding -> profiles/identifiers -> relationships
        |
        v
   score_profile_against_person() : explainable score
        |
        v
   next_level_tasks() if depth allows and automation is enabled
        |
        v
   _finish_search_if_done() : SUCCESS / PARTIAL / FAILED
```

### Differential search

`_recently_done()` (in `services/orchestration.py`) excludes any
(plugin, target type, normalised value) triple that already succeeded within
`FRESHNESS_DAYS` (7 by default). Adding `jean62` to a case file that already
contained `jdupont` therefore only triggers the work concerning `jean62`.
`force=true` bypasses this mechanism.

### Depth

`MAX_DEPTH = 4`. A level-N run can only trigger level N+1 when:

- the depth requested on the campaign allows it;
- `investigation.automation_enabled` is true;
- the discovered identifier is of an actionable type (username, email, phone,
  domain);
- the plugin is not "contextual" (Toutatis is never triggered automatically at
  depth).

## 3. Data model

```text
users ──< investigations ──< persons ──< identifiers
                │                │        │
                │                ├──< usernames >── platforms
                │                ├──< social_profiles >── platforms
                │                ├──< findings >── sources
                │                ├──< contradictions
                │                └──< timeline_events
                ├──< organizations
                ├──< relationships          (graph: (type, id) -> (type, id))
                ├──< sources
                ├──< notes
                └──< searches ──< plugin_runs ──< search_results

plugins, plugin_secrets, tags, person_tags, refresh_tokens, audit_logs
```

Design notes:

- **`normalized_value` everywhere.** `06 12 34 56 78`, `+33612345678` and
  `06.12.34.56.78` all converge to `+33612345678`. `j.dupont`, `j_dupont` and
  `@JDupont` all converge to `jdupont`. The original value is still displayed.
- **`usernames` is a dedicated table**, not just a row in `identifiers`: a
  username carries an *optional* platform, a URL, a status, a source, a
  discovery date, a hypothetical flag and the rule that generated it.
- **`relationships` is polymorphic** (`source_type`/`source_ref` ->
  `target_type`/`target_ref`): the graph links people, identifiers, usernames,
  profiles, platforms and organisations without one join table per pair.
- **`findings.dedup_key`** makes ingestion idempotent: re-running a plugin
  creates no duplicates and never resurrects an item an analyst rejected.
- **`sources.reliability`** feeds the scoring engine; per-category defaults live
  in `services/scoring.py::SOURCE_RELIABILITY`.

### Portable column types

`app/db/base.py` defines three portable types:

- `GUID` — native `UUID` on PostgreSQL, `CHAR(32)` elsewhere;
- `JSONDict` — `JSONB` on PostgreSQL, `JSON` elsewhere;
- `TZDateTime` — always returns timezone-aware UTC values, which removes the
  "can't compare offset-naive and offset-aware datetimes" class of bug on token
  expiry and retention comparisons.

PostgreSQL remains the production target (indexable JSONB, native UUID); the
portability also lets the integration suite run on SQLite with no infrastructure.

## 4. Correlation

`services/correlation.py` builds a `PersonIndex` (names, name tokens, usernames,
emails, phone numbers, cities, organisations, domains, platform user IDs) and
compares a discovered profile against that index. Every match produces a named
signal; `services/scoring.py` turns signals into points.

Built-in guardrails:

- a single weak signal triggers the `single_weak_signal` penalty;
- a profile found through a hypothetical variant triggers `variant_only`;
- an incompatible location triggers `location_conflict`;
- a human decision (`CONFIRMED` / `REJECTED`) overrides everything else.

The result always exposes `breakdown`: the UI renders the calculation line by
line.

## 5. Contradictions

`identifiers.py::_detect_contradiction` applies to single-valued fields (city,
department, region, country, address, date of birth). Two different values
create an **unresolved** `contradictions` row and a timeline event. No value is
overwritten, none is favoured. Resolution goes through
`POST /contradictions/{id}/resolve` and is recorded in the audit log.

## 6. Graph

`services/graph.py` produces `{nodes, edges, stats}`, directly consumable by
Cytoscape.js. Available filters: by person, by node type, by minimum score.
`is_variant` nodes are flagged so the UI can render them differently.

## 7. Async jobs

Celery + Redis. One queue per tool (`sherlock`, `holehe`, `phoneinfoga`,
`toutatis`, `websearch`) plus the `default` queue for orchestration.
`task_acks_late` and `worker_prefetch_multiplier=1` prevent a worker from
hoarding long-running tasks.

Periodic tasks (`beat`):

- `reconcore.health_check` — hourly, probes each enabled plugin inside its own
  worker;
- `reconcore.apply_retention` — nightly, permanently deletes according to
  `DATA_RETENTION_DAYS` / `AUDIT_LOG_RETENTION_DAYS` and per-case retention
  dates.

## 8. Adding a plugin

1. Create `backend/app/plugins/<name>/` with `plugin.py` and `manifest.json`.
2. Subclass `OSINTPlugin` and implement `check_health`, `execute`, `normalize`
   (and `validate` if you need specific guardrails).
3. Expose `PLUGIN = MyClass` at the end of the module.
4. Declare `queue = "<name>"` and create the matching worker in
   `docker-compose.yml` + `docker/worker-<name>.Dockerfile`.
5. Audit it: `osint plugin audit <name>`.
6. Enable it: `osint plugin enable <name>`.

Discovery is explicit (`pkgutil.iter_modules` over `app/plugins`): no code is
loaded from an arbitrary path, and nothing is downloaded at runtime.
