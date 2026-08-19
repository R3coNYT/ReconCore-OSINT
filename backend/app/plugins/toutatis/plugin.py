"""Toutatis plugin (Instagram) - OPTIONAL, DISABLED BY DEFAULT.

Upstream: https://github.com/megadose/toutatis (GPL-3.0)

Usage rules enforced by the platform:
  * the tool requires an Instagram `sessionid` cookie from an EXISTING account;
  * ReconCore NEVER asks for an Instagram password and stores none;
  * ReconCore automates no authentication and circumvents no protection: the
    cookie is supplied manually by the administrator, who obtains it from their
    own browser session;
  * the cookie is encrypted at rest (Fernet) and passed to the subprocess over
    STDIN only;
  * the plugin can be switched off entirely (`TOUTATIS_ENABLED=false`), in which
    case no upstream code is called at all;
  * using an account to collect data may breach Instagram's terms of service and
    expose that account to suspension. That responsibility lies with the
    operator: the risk is displayed in the UI before any activation.
"""
from __future__ import annotations

import json

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
from app.plugins.runner import python_executable, run_command

DRIVER = "app.plugins.toutatis.driver"

#: Fields upstream reports as partially obfuscated.
OBFUSCATED_FIELDS = {
    "obfuscated_email": "recovery_email_obfuscated",
    "obfuscated_phone": "recovery_phone_obfuscated",
}


class ToutatisPlugin(OSINTPlugin):
    name = "toutatis"
    version = "1.0.0"
    description = (
        "Public information attached to an Instagram account. Requires a session "
        "cookie supplied manually by an administrator."
    )
    repository = "https://github.com/megadose/toutatis"
    license = "GPL-3.0"
    supported_identifiers = [IdentifierType.USERNAME.value]
    queue = "toutatis"
    requests_per_minute = 4
    concurrency = 1
    timeout_seconds = 240
    enabled_by_default = False
    requires_secrets = ["sessionid"]
    risk_notes = [
        "Requires an Instagram sessionid cookie: never supply a password.",
        "Use may breach Instagram's terms of service and get the account suspended.",
        "The cookie is encrypted at rest and passed over STDIN only.",
        "Disabled by default: an administrator must enable it explicitly.",
    ]

    def check_health(self) -> HealthStatus:
        if not settings.toutatis_enabled:
            return HealthStatus(
                ok=False,
                message="Plugin disabled by configuration (TOUTATIS_ENABLED=false)",
                details={"disabled": True},
            )
        probe = run_command(
            [python_executable(), "-c", "import toutatis; print('installed')"],
            timeout=60,
        )
        if not probe.ok:
            return HealthStatus(
                ok=False,
                message=(
                    "toutatis is not installed in this worker. Install it: pip install toutatis"
                ),
            )
        return HealthStatus(
            ok=True,
            message=(
                "toutatis available. A sessionid cookie must be configured "
                "(Settings > Plugins > Toutatis) before a search can run."
            ),
        )

    def execute(self, target: Target) -> RawResult:
        raw = RawResult()
        if not settings.toutatis_enabled:
            raw.error = "Toutatis plugin disabled (TOUTATIS_ENABLED=false)"
            return raw

        username = target.value.strip().lstrip("@")
        if not username:
            raw.error = "empty Instagram username"
            return raw

        # The orchestrator injects the decrypted secret into the target context;
        # it only ever exists in the worker's memory.
        sessionid = target.context.get("secrets", {}).get("sessionid")
        if not sessionid:
            raw.error = (
                "sessionid cookie missing. Configure it in the interface "
                "(Settings > Plugins > Toutatis) or with "
                "`osint plugin secret set toutatis sessionid`."
            )
            return raw

        raw.logs.append(f"[INFO] Starting Toutatis for Instagram user {username}")
        result = run_command(
            [python_executable(), "-m", DRIVER],
            timeout=target.context.get("timeout") or self.timeout_seconds,
            stdin_data=json.dumps({"username": username, "sessionid": sessionid}),
        )

        if result.timed_out:
            raw.error = "Toutatis timed out"
            return raw

        payload = _last_json(result.stdout)
        if payload is None:
            raw.error = f"unreadable Toutatis output: {result.stderr.strip()[:400]}"
            return raw
        if payload.get("error"):
            raw.error = str(payload["error"])[:500]
            return raw

        raw.items = [payload]
        raw.meta["fields_found"] = len(payload.get("fields", {}))
        raw.logs.append(f"[INFO] {raw.meta['fields_found']} field(s) extracted")
        return raw

    def normalize(self, raw: RawResult, target: Target) -> list[NormalizedItem]:
        items: list[NormalizedItem] = []
        for entry in raw.items:
            fields: dict = entry.get("fields", {}) or {}
            username = entry.get("username") or target.value
            url = f"https://instagram.com/{username}"

            profile_payload = {
                "type": "social_profile",
                "platform": "Instagram",
                "username": fields.get("userame") or fields.get("username") or username,
                "url": url,
                "platform_user_id": fields.get("user_id"),
                "display_name": fields.get("full_name"),
                "bio": fields.get("bio"),
                "external_url": fields.get("external_url"),
                "is_verified": _as_bool(fields.get("verified")),
                "is_private": _as_bool(fields.get("is_private")),
                "is_business": _as_bool(fields.get("is_business")),
                "followers": _as_int(fields.get("follower") or fields.get("followers")),
                "following": _as_int(fields.get("following")),
                "posts_count": _as_int(fields.get("number_of_posts")),
                "public_email": fields.get("public_email"),
                "public_phone": fields.get("public_phone_number"),
                "avatar_url": fields.get("profile_picture") or fields.get("picture"),
            }
            items.append(
                NormalizedItem(
                    kind=FindingType.PROFILE_METADATA.value,
                    title=f"Instagram: {username}",
                    payload={k: v for k, v in profile_payload.items() if v is not None},
                    source=SourceRef(
                        kind=SourceKind.OFFICIAL_WEBSITE.value,
                        url=url,
                        title="Instagram (via Toutatis)",
                        description="Public profile data exactly as returned by the platform.",
                        reliability=0.90,
                        raw_reference="toutatis:profile",
                    ),
                    confidence=0.75,
                    dedup_key=f"social:instagram:{str(username).lower()}",
                    derived_identifiers=_derived(profile_payload),
                )
            )

            for source_field, target_field in OBFUSCATED_FIELDS.items():
                value = fields.get(source_field)
                if not value:
                    continue
                items.append(
                    NormalizedItem(
                        kind=FindingType.OBFUSCATED_CONTACT.value,
                        title=f"Instagram: {target_field.replace('_', ' ')}",
                        payload={
                            "platform": "Instagram",
                            "username": username,
                            target_field: value,
                            "certain": False,
                        },
                        source=SourceRef(
                            kind=SourceKind.OFFICIAL_WEBSITE.value,
                            url=url,
                            title="Instagram (via Toutatis)",
                            reliability=0.85,
                            raw_reference=f"toutatis:{source_field}",
                        ),
                        confidence=0.40,
                        dedup_key=f"toutatis:{source_field}:{str(username).lower()}",
                        warnings=[
                            "Partially obfuscated value: useful for cross-referencing, "
                            "not for direct identification."
                        ],
                    )
                )
        return items


def _derived(profile: dict) -> list[dict]:
    derived: list[dict] = []
    if profile.get("public_email"):
        derived.append(
            {
                "type": IdentifierType.EMAIL.value,
                "value": profile["public_email"],
                "confidence": 0.70,
                "status": "PROBABLE",
            }
        )
    if profile.get("public_phone"):
        derived.append(
            {
                "type": IdentifierType.PHONE.value,
                "value": profile["public_phone"],
                "confidence": 0.70,
                "status": "PROBABLE",
            }
        )
    if profile.get("external_url"):
        derived.append(
            {
                "type": IdentifierType.WEBSITE.value,
                "value": profile["external_url"],
                "confidence": 0.65,
                "status": "PROBABLE",
            }
        )
    return derived


def _as_bool(value) -> bool | None:
    if value is None:
        return None
    return str(value).strip().lower() in {"true", "yes", "1"}


def _as_int(value) -> int | None:
    if value is None:
        return None
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    return int(digits) if digits else None


def _last_json(stdout: str) -> dict | None:
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


PLUGIN = ToutatisPlugin
