"""Holehe driver, executed inside the dedicated worker.

The plugin runs this script as a subprocess with a timeout and a reduced
environment (see `app.plugins.runner`). It has no access to the database nor to
application secrets: it writes one JSON object to stdout and nothing else.

Usage: python -m app.plugins.holehe.driver <email> [--concurrency N]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys

EXIT_MISSING_DEP = 3


async def _run(email: str, concurrency: int, per_module_timeout: float) -> dict:
    import httpx
    from holehe.core import import_submodules

    modules = import_submodules("holehe.modules")
    functions = []
    for path, module in modules.items():
        # Concrete modules look like holehe.modules.<category>.<site>
        if len(path.split(".")) > 3:
            func_name = path.split(".")[-1]
            func = module.__dict__.get(func_name)
            if callable(func):
                functions.append((func_name, func))

    out: list[dict] = []
    errors: list[str] = []
    semaphore = asyncio.Semaphore(concurrency)

    async with httpx.AsyncClient(timeout=per_module_timeout) as client:

        async def call(name, func) -> None:
            async with semaphore:
                bucket: list[dict] = []
                try:
                    await asyncio.wait_for(
                        func(email, client, bucket), timeout=per_module_timeout
                    )
                except TimeoutError:
                    errors.append(f"{name}: timeout")
                    return
                except Exception as exc:  # upstream module is fragile: isolate it
                    errors.append(f"{name}: {type(exc).__name__}: {exc}")
                    return
                out.extend(bucket)

        await asyncio.gather(*(call(n, f) for n, f in functions))

    return {
        "email": email,
        "modules_total": len(functions),
        "modules_errors": errors,
        "results": out,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Holehe driver (JSON on stdout)")
    parser.add_argument("email")
    parser.add_argument("--concurrency", type=int, default=10)
    parser.add_argument("--module-timeout", type=float, default=20.0)
    args = parser.parse_args()

    try:
        import holehe  # noqa: F401
    except ImportError:
        print(
            json.dumps(
                {
                    "error": (
                        "holehe is not installed in this worker. "
                        "Install it: pip install holehe"
                    )
                }
            )
        )
        return EXIT_MISSING_DEP

    try:
        payload = asyncio.run(
            _run(args.email, args.concurrency, args.module_timeout)
        )
    except Exception as exc:  # pragma: no cover - defensive
        print(json.dumps({"error": f"{type(exc).__name__}: {exc}"}))
        return 1

    json.dump(payload, sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
