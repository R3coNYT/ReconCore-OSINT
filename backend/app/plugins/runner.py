"""Controlled execution of external tools.

This module is the ONLY place where a third-party process is started. It
enforces:
  * no shell (`shell=False`, explicit argv) -> no command injection;
  * an allow-listed environment -> no secret leakage;
  * a disposable temporary working directory -> no writes elsewhere;
  * a hard timeout plus process-group kill -> no zombie tasks;
  * memory/CPU/process limits via `resource` where the platform supports it.

Architecture reminder: this code runs INSIDE the worker container dedicated to
the tool (worker-sherlock, worker-holehe, ...). Those containers have no access
to the host, to the Docker socket, or to the database.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

#: Environment variables passed through to external tools.
ENV_ALLOWLIST = (
    "PATH",
    "HOME",
    "LANG",
    "LC_ALL",
    "PYTHONPATH",
    "PYTHONUNBUFFERED",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "REQUESTS_CA_BUNDLE",
    "TMPDIR",
)

#: Limits applied to the child process (Unix only).
#:
#: Memory is deliberately NOT limited here. RLIMIT_AS caps virtual address
#: space, and every thread reserves ~8 MB of it, so a 1 GB cap strangles any
#: multithreaded tool with "can't start new thread" - Sherlock opens one
#: thread per site. The container's `mem_limit` already caps *resident*
#: memory, which is the limit that actually matters; pass `memory_mb`
#: explicitly only for a single-threaded tool that needs it.
DEFAULT_MEMORY_MB = None
DEFAULT_MAX_PROCESSES = 256


@dataclass
class CommandResult:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out


def build_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k in ENV_ALLOWLIST}
    env.setdefault("PYTHONUNBUFFERED", "1")
    env.setdefault("HOME", tempfile.gettempdir())
    # Optional outbound proxy (legitimate use: dedicated IP, logging).
    if settings.osint_http_proxy:
        env["HTTP_PROXY"] = settings.osint_http_proxy
    if settings.osint_https_proxy:
        env["HTTPS_PROXY"] = settings.osint_https_proxy
    if extra:
        env.update(extra)
    return env


def _preexec(memory_mb: int | None, max_processes: int):
    """POSIX limits applied just before the child exec."""
    if os.name != "posix":  # pragma: no cover - Windows / local dev
        return None

    import resource  # local import: unavailable on Windows

    def apply() -> None:
        os.setsid()
        if memory_mb is not None:
            limit = memory_mb * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
        resource.setrlimit(resource.RLIMIT_NPROC, (max_processes, max_processes))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))

    return apply


def run_command(
    argv: list[str],
    *,
    timeout: int | None = None,
    env_extra: dict[str, str] | None = None,
    stdin_data: str | None = None,
    memory_mb: int | None = DEFAULT_MEMORY_MB,
    max_processes: int = DEFAULT_MAX_PROCESSES,
    cwd: str | None = None,
) -> CommandResult:
    """Run an external binary without a shell, with timeout and limits."""
    if not argv:
        raise ValueError("empty argv")

    timeout = timeout or settings.plugin_default_timeout
    workdir = cwd or tempfile.mkdtemp(prefix="reconcore-")
    owns_workdir = cwd is None

    logger.info("Running external tool: %s (timeout=%ss)", argv[0], timeout)
    try:
        proc = subprocess.Popen(  # noqa: S603 - explicit argv, shell=False
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.PIPE if stdin_data else subprocess.DEVNULL,
            env=build_env(env_extra),
            cwd=workdir,
            text=True,
            shell=False,
            preexec_fn=_preexec(memory_mb, max_processes),
        )
        try:
            stdout, stderr = proc.communicate(input=stdin_data, timeout=timeout)
            return CommandResult(proc.returncode, stdout or "", stderr or "")
        except subprocess.TimeoutExpired:
            _kill(proc)
            stdout, stderr = proc.communicate()
            return CommandResult(
                -1, stdout or "", (stderr or "") + "\n[timeout]", timed_out=True
            )
    except FileNotFoundError as exc:
        return CommandResult(-1, "", f"binary not found: {exc}")
    finally:
        if owns_workdir:
            shutil.rmtree(workdir, ignore_errors=True)


def _kill(proc: subprocess.Popen) -> None:
    try:
        if os.name == "posix":
            os.killpg(os.getpgid(proc.pid), 9)
        else:  # pragma: no cover - Windows dev
            proc.kill()
    except (ProcessLookupError, PermissionError, OSError):
        proc.kill()


def tool_available(binary: str) -> bool:
    return shutil.which(binary) is not None


def python_module_available(module: str) -> bool:
    from importlib.util import find_spec

    try:
        return find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def python_executable() -> str:
    return sys.executable or "python3"
