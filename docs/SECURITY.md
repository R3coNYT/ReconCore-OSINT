# Security

## 1. Threat model

| Threat                                              | Mitigation                                                              |
|-----------------------------------------------------|-------------------------------------------------------------------------|
| Compromised third-party tool (dependency, update)   | Dedicated container, no DB access, no host access, no Docker socket     |
| Tampered tool output (injection into stored data)   | The plugin persists nothing; the orchestrator validates and normalises  |
| Command injection through a target value            | `shell=False`, explicit argv, no string ever handed to a shell          |
| Secret exfiltration by a tool                       | Allow-listed environment; no DB credentials in tool workers             |
| Database theft                                       | Plugin secrets encrypted (Fernet), passwords hashed with Argon2id       |
| Stolen access token                                  | Short-lived JWT (30 min), rotating revocable refresh, `tokens_valid_after` |
| Credential stuffing / brute force                   | Application rate limiting plus Nginx (10 req/min on `/auth/login`)      |
| Abusing the platform against a third-party service  | Self-imposed per-plugin quotas, low default concurrency                 |
| Unauthorised access to case files                    | RBAC ADMIN / ANALYST / READ_ONLY, audit log on every mutation           |
| Indefinite retention of personal data               | Configurable retention + permanent deletion + export                    |

## 2. Third-party tool isolation

Rules applied in `docker-compose.yml` to every tool worker:

```yaml
read_only: true
tmpfs: [/tmp, /home]
security_opt: [no-new-privileges:true]
cap_drop: [ALL]
pids_limit: 256
mem_limit: 1g
cpus: 1.0
networks: [bus, egress]     # never `data`
```

What does not exist anywhere in this repository, and must never appear:

```text
-v /:/host
--privileged
/var/run/docker.sock
```

The static audit (`osint plugin audit`) flags all three as `CRITICAL`, and
`tests/test_infrastructure.py` fails the build if any of them reappears in the
compose file.

At process level, `app/plugins/runner.py` additionally enforces:

- `shell=False` with explicit argv — no command injection;
- an allow-listed environment (`ENV_ALLOWLIST`);
- a disposable temporary working directory;
- a hard timeout followed by `killpg` of the process group;
- `RLIMIT_AS`, `RLIMIT_NPROC`, `RLIMIT_CORE` on POSIX.

### Accepted limitation

The Redis broker carries targets and — for Toutatis only — the decrypted session
cookie on its way to the worker. That bus sits on an `internal` Docker network
(no internet egress, no published port). If your threat model includes broker
compromise, leave Toutatis disabled: it is the only plugin affected, and it is
disabled by default.

## 3. Authentication and sessions

- **Argon2id** (`time_cost=3`, `memory_cost=64 MiB`, `parallelism=4`), with
  automatic re-hashing when the parameters change.
- Password policy: at least 12 characters, upper case, lower case, digit,
  special character.
- **Short-lived access JWTs** (30 min by default); **opaque refresh tokens**
  stored only as SHA-256 digests, rotated on every use (the previous one is
  revoked immediately) and individually revocable.
- `tokens_valid_after` invalidates every already-issued token at once (password
  change, account deactivation, global logout).
- In the browser, tokens live in `sessionStorage`: nothing survives closing the
  tab on a shared machine.
- Identical error message for "unknown account" and "wrong password": no account
  enumeration.

## 4. Secrets

- Symmetric **Fernet** encryption (`SECRETS_ENCRYPTION_KEY`).
- The API **never** returns a secret value: only whether it is set, plus a
  masked preview (`****abcd`).
- Storing a secret whose key name suggests a password is refused outright.
- Without `SECRETS_ENCRYPTION_KEY`, writing a secret is **refused** explicitly
  rather than silently degraded.
- In production the application refuses to start without that key, or with the
  development `SECRET_KEY`.

### Rotating `SECRETS_ENCRYPTION_KEY`

Encryption is not versioned: changing the key makes existing secrets unreadable
(explicit `SecretsUnavailable`, never silent corruption). Procedure: delete the
secrets (`osint plugin secret delete ...`), change the key, restart, re-enter
the secrets.

## 5. Application-level protections

| Vector           | Mitigation                                                                |
|------------------|---------------------------------------------------------------------------|
| SQL injection    | SQLAlchemy ORM, parameterised queries, no string interpolation             |
| XSS              | React escapes by default; no `dangerouslySetInnerHTML`; strict CSP         |
| CSRF             | Header-based `Authorization`, no session cookie                            |
| Clickjacking     | `X-Frame-Options: DENY` plus `frame-ancestors 'none'`                      |
| MIME sniffing    | `X-Content-Type-Options: nosniff`                                          |
| Referrer leakage | `Referrer-Policy: no-referrer`                                             |
| Transport        | `Strict-Transport-Security` in production (terminate TLS in front)         |
| Enumeration      | Uniform authentication responses                                           |

Discovered external links open with `target="_blank"` and
`rel="noreferrer noopener"`: the target page learns nothing about the platform.

## 6. Audit log

Table `audit_logs`, append-only from the application's perspective. Each entry
keeps: timestamp, user, action, object type and id, IP (via `X-Forwarded-For`),
user agent, JSON detail.

Recorded actions: login and failed login, global logout, password change,
account create/update/delete, case file create/update/delete, identifier added,
search launched, decision on a finding or a profile, contradiction resolved,
person merge, case export, plugin enable/disable, plugin audit, secret write and
delete.

## 7. Personal data

- **Purpose** — each case file's `legal_basis` field documents the framework for
  the collection. It is free text, but leaving it empty should raise a question.
- **Minimisation** — the platform favours correlation quality over raw volume. A
  rate-limited result is not turned into a fact; an obfuscated value is not
  reconstructed.
- **Accuracy** — explicit contradictions, human validation, justified scores,
  timestamped and re-checkable sources.
- **Retention** — `DATA_RETENTION_DAYS` (global) and `retention_until` (per case
  file). The `reconcore.apply_retention` task deletes permanently — no bin, no
  soft delete.
- **Access / portability** — complete JSON, CSV and PDF export of a case file.
- **Traceability** — the audit log answers "who accessed or changed what, and
  when".

## 8. Deployment

Before exposing anything on a network:

1. Run `python scripts/gen_secrets.py --write`, then verify no `CHANGE_ME`
   remains in `.env`.
2. Set `RECONCORE_ENV=production` (disables `/docs`, enables HSTS, hardens the
   startup checks).
3. Terminate TLS in front of Nginx (reverse proxy or mounted certificate).
4. Restrict `BACKEND_CORS_ORIGINS` to the frontend's real origin.
5. Publish only the Nginx port; postgres and redis expose nothing.
6. Set `DATA_RETENTION_DAYS` according to your policy.
7. Audit every plugin before enabling it; leave disabled whatever you do not
   need.
8. Back up `postgres_data` **and** `SECRETS_ENCRYPTION_KEY` (a backup without
   the key makes the secrets unrecoverable).

## 9. Reporting a vulnerability

Open a private issue with the repository maintainer, and do not publish
exploitable detail until a fix is available.
