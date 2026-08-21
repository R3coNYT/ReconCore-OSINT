"""Resource limits applied to external tools.

Regression tests for a silent failure found in production: `RLIMIT_AS` caps
*virtual* address space, and every thread reserves ~8 MB of it. A 1 GB cap
therefore killed Sherlock with "can't start new thread" after the banner, and
because Sherlock exits 1 on crash - a code the plugin treated as "nothing
found" - the run was stored as SUCCESS with zero results.
"""
from __future__ import annotations

import inspect

from app.plugins import runner
from app.plugins.base import RawResult, Target
from app.plugins.runner import CommandResult
from app.plugins.sherlock.plugin import SherlockPlugin


def test_address_space_is_not_capped_by_default() -> None:
    """Capping virtual memory breaks every multithreaded tool."""
    assert runner.DEFAULT_MEMORY_MB is None, (
        "RLIMIT_AS must stay opt-in: the container's mem_limit caps resident "
        "memory without breaking threads"
    )
    signature = inspect.signature(runner.run_command)
    assert signature.parameters["memory_mb"].default is None


def test_process_limit_stays_in_place() -> None:
    """The fork-bomb guard is still there, just sized realistically."""
    assert runner.DEFAULT_MAX_PROCESSES >= 128


def test_preexec_skips_the_memory_limit_when_unset(monkeypatch) -> None:
    monkeypatch.setattr(runner.os, "name", "posix")
    applied: list[tuple[str, tuple[int, int]]] = []

    class _FakeResource:
        RLIMIT_AS, RLIMIT_NPROC, RLIMIT_CORE = 1, 2, 3

        @staticmethod
        def setrlimit(which, limits):
            names = {1: "AS", 2: "NPROC", 3: "CORE"}
            applied.append((names[which], limits))

    monkeypatch.setitem(__import__("sys").modules, "resource", _FakeResource)
    monkeypatch.setattr(runner.os, "setsid", lambda: None, raising=False)

    runner._preexec(None, 256)()
    assert [name for name, _ in applied] == ["NPROC", "CORE"]

    applied.clear()
    runner._preexec(512, 256)()
    assert [name for name, _ in applied] == ["AS", "NPROC", "CORE"]


def _run_with(monkeypatch, returncode: int, stdout: str, stderr: str) -> RawResult:
    plugin = SherlockPlugin()
    monkeypatch.setattr(plugin, "_base_argv", lambda: ["sherlock"])
    monkeypatch.setattr(plugin, "_parse_csv", lambda outdir, username: [])
    monkeypatch.setattr(
        "app.plugins.sherlock.plugin.run_command",
        lambda *a, **k: CommandResult(returncode, stdout, stderr),
    )
    return plugin.execute(Target(type="USERNAME", value="jdupont"))


def test_a_crash_is_reported_not_swallowed(monkeypatch) -> None:
    """Exit 1 with no results is a failure, not an empty answer."""
    raw = _run_with(
        monkeypatch,
        returncode=1,
        stdout="[*] Checking username jdupont on:\n",
        stderr="RuntimeError: can't start new thread",
    )
    assert raw.error is not None
    assert "code 1" in raw.error
    assert "can't start new thread" in raw.error


def test_stderr_is_kept_in_the_run_logs(monkeypatch) -> None:
    """So the next diagnosis does not need a live reproduction."""
    raw = _run_with(
        monkeypatch, returncode=1, stdout="", stderr="something went wrong"
    )
    assert any("[STDERR]" in line for line in raw.logs)


def test_finding_nothing_is_a_valid_answer(monkeypatch) -> None:
    """Sherlock exits 0 even when no account exists: that is not an error."""
    raw = _run_with(
        monkeypatch,
        returncode=0,
        stdout="[*] Checking username jdupont on:\n[*] Search completed with 0 results\n",
        stderr="",
    )
    assert raw.error is None
    assert raw.items == []


def test_results_are_still_parsed_on_success(monkeypatch) -> None:
    raw = _run_with(
        monkeypatch,
        returncode=0,
        stdout="[+] GitHub: https://github.com/jdupont\n",
        stderr="",
    )
    assert raw.error is None
    assert raw.items == [{"platform": "GitHub", "url": "https://github.com/jdupont"}]
