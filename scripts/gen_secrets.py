#!/usr/bin/env python3
"""Generate the sensitive values that belong in `.env`.

Usage:
    python scripts/gen_secrets.py            # print the values
    python scripts/gen_secrets.py --write    # update .env in place

Nothing is sent anywhere: every value is generated locally with `secrets`
(the system CSPRNG).
"""
from __future__ import annotations

import argparse
import base64
import os
import re
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def generate() -> dict[str, str]:
    return {
        "SECRET_KEY": secrets.token_hex(32),
        "SECRETS_ENCRYPTION_KEY": base64.urlsafe_b64encode(os.urandom(32)).decode(),
        "POSTGRES_PASSWORD": secrets.token_urlsafe(24),
        "FIRST_ADMIN_PASSWORD": secrets.token_urlsafe(18) + "aA1!",
    }


def write_env(values: dict[str, str]) -> Path:
    env_path = ROOT / ".env"
    if not env_path.exists():
        env_path.write_text((ROOT / ".env.example").read_text(encoding="utf-8"), encoding="utf-8")
        print("[.env created from .env.example]")

    content = env_path.read_text(encoding="utf-8")
    for key, value in values.items():
        pattern = re.compile(rf"^{key}=.*$", re.MULTILINE)
        if pattern.search(content):
            content = pattern.sub(f"{key}={value}", content)
        else:
            content += f"\n{key}={value}\n"
    env_path.write_text(content, encoding="utf-8")
    return env_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="Write straight into .env")
    args = parser.parse_args()

    values = generate()
    if args.write:
        path = write_env(values)
        print(f"Values written to {path}")
        print("Initial admin password:", values["FIRST_ADMIN_PASSWORD"])
        print("Change it on first login.")
    else:
        for key, value in values.items():
            print(f"{key}={value}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
