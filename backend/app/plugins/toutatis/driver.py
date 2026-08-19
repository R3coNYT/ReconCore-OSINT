"""Toutatis driver (OPTIONAL Instagram plugin).

The session cookie is read from STDIN. It is never passed as a command-line
argument (where it would show up in the process list) and never written to disk.
It is not logged.

Input:  JSON on stdin  -> {"username": "...", "sessionid": "..."}
Output: JSON on stdout -> {"fields": {...}, "raw_text": "..."} or {"error": ...}
"""
from __future__ import annotations

import contextlib
import io
import json
import re
import sys

EXIT_MISSING_DEP = 3
KV_LINE = re.compile(r"^\s*([A-Za-z][A-Za-z0-9 _./'-]{2,40})\s*:\s*(.+?)\s*$")


def _parse_output(text: str) -> dict:
    """Turn Toutatis text output into a key/value dictionary."""
    fields: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith(("=", "-", "*")):
            continue
        # Some lines pack several fields separated by '|'.
        for chunk in line.split("|"):
            match = KV_LINE.match(chunk)
            if match:
                key = match.group(1).strip().lower().replace(" ", "_")
                value = match.group(2).strip()
                if value and value.lower() not in {"none", "null", "-"}:
                    fields[key] = value
    return fields


def _run_python_api(username: str, sessionid: str) -> tuple[dict, str] | None:
    try:
        from toutatis.core import getInfo  # type: ignore
    except Exception:
        return None
    buffer = io.StringIO()
    try:
        with contextlib.redirect_stdout(buffer):
            getInfo(username, sessionid)
    except TypeError:
        # The signature differs between upstream versions.
        try:
            with contextlib.redirect_stdout(buffer):
                getInfo(username, sessionid, False)
        except Exception as exc:
            return {}, f"[Toutatis API error] {type(exc).__name__}: {exc}"
    except Exception as exc:
        return {}, f"[Toutatis API error] {type(exc).__name__}: {exc}"
    text = buffer.getvalue()
    return _parse_output(text), text


def _run_cli(username: str, sessionid: str) -> tuple[dict, str]:
    import subprocess

    proc = subprocess.run(  # noqa: S603 - explicit argv, dedicated container
        ["toutatis", "-u", username, "-s", sessionid],
        capture_output=True,
        text=True,
        timeout=180,
        shell=False,
    )
    text = (proc.stdout or "") + (proc.stderr or "")
    return _parse_output(text), text


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        print(json.dumps({"error": f"invalid input: {exc}"}))
        return 1

    username = (payload.get("username") or "").strip().lstrip("@")
    sessionid = (payload.get("sessionid") or "").strip()
    if not username:
        print(json.dumps({"error": "missing username"}))
        return 1
    if not sessionid:
        print(json.dumps({"error": "missing sessionid (plugin not configured)"}))
        return 1

    try:
        import toutatis  # noqa: F401
    except ImportError:
        print(
            json.dumps(
                {
                    "error": (
                        "toutatis is not installed in this worker. "
                        "Install it: pip install toutatis"
                    )
                }
            )
        )
        return EXIT_MISSING_DEP

    result = _run_python_api(username, sessionid)
    if result is None:
        try:
            result = _run_cli(username, sessionid)
        except FileNotFoundError:
            print(json.dumps({"error": "toutatis binary not found"}))
            return EXIT_MISSING_DEP
        except Exception as exc:
            print(json.dumps({"error": f"{type(exc).__name__}: {exc}"}))
            return 1

    fields, raw_text = result
    # The cookie must never leak back out, not even by accident.
    raw_text = raw_text.replace(sessionid, "<sessionid redacted>")
    json.dump({"username": username, "fields": fields, "raw_text": raw_text[:20000]}, sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
