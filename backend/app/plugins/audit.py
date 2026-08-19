"""Static security audit of plugins and third-party tools.

This module implements the checklist applied BEFORE enabling an external tool:
licence, project activity, dependencies, system calls, network, dynamic
downloads, code execution, hardcoded secrets, Dockerfiles, GitHub Actions.

ACKNOWLEDGED LIMITATION: this is static analysis, a DECISION AID. It does not
prove the absence of malicious behaviour. A "no signal" result does not excuse
reading the code, nor running it in a sandbox.
"""
from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from app.models.enums import RiskLevel

PLUGINS_DIR = Path(__file__).parent

#: Patterns searched in the source (outside the AST) and their severity.
PATTERNS: list[tuple[str, str, str, str]] = [
    # (code, regex, severity, explanation)
    ("docker_socket", r"/var/run/docker\.sock", "CRITICAL",
     "Docker socket access: equivalent to root on the host"),
    ("privileged", r"--privileged\b", "CRITICAL",
     "Privileged container requested"),
    ("host_mount", r"-v\s+/:(/|\s)", "CRITICAL",
     "Host root filesystem mounted"),
    ("curl_pipe_shell", r"(curl|wget)[^\n|]*\|\s*(sudo\s+)?(ba)?sh", "CRITICAL",
     "Downloads then directly executes a script"),
    ("shell_true", r"shell\s*=\s*True", "HIGH",
     "subprocess with shell=True: command injection risk"),
    ("os_system", r"\bos\.system\s*\(", "HIGH", "os.system call"),
    ("eval_exec", r"\b(eval|exec)\s*\(", "HIGH",
     "Executes dynamically built code"),
    ("pickle_load", r"\bpickle\.loads?\s*\(", "HIGH",
     "pickle deserialisation: arbitrary code execution possible"),
    ("dynamic_import", r"\b__import__\s*\(", "MEDIUM", "Dynamic import"),
    ("subprocess", r"\bsubprocess\.(run|Popen|call|check_output)", "MEDIUM",
     "Starts an external process"),
    ("network", r"\b(requests\.|httpx\.|aiohttp\.|urllib\.request|socket\.socket)",
     "LOW", "Outbound network access"),
    ("filesystem_write", r"\bopen\s*\([^)]*['\"][wa]", "LOW",
     "Writes a file"),
    ("env_read", r"\bos\.environ\b", "LOW", "Reads environment variables"),
    ("temp_download", r"\b(urlretrieve|download_url|wget\s+http)", "HIGH",
     "Downloads a file at runtime"),
]

#: Hardcoded-secret detection. Deliberately conservative (few false positives).
SECRET_PATTERNS: list[tuple[str, str]] = [
    ("aws_access_key", r"AKIA[0-9A-Z]{16}"),
    ("github_token", r"gh[pousr]_[A-Za-z0-9]{30,}"),
    ("slack_token", r"xox[baprs]-[A-Za-z0-9-]{10,}"),
    ("private_key", r"-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    ("generic_api_key", r"(?i)(api[_-]?key|secret|token)\s*[:=]\s*['\"][A-Za-z0-9/+_\-]{24,}['\"]"),
]

_SEVERITY_ORDER = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}


@dataclass
class Signal:
    code: str
    severity: str
    file: str
    line: int
    excerpt: str
    explanation: str

    def as_dict(self) -> dict:
        return {
            "code": self.code,
            "severity": self.severity,
            "file": self.file,
            "line": self.line,
            "excerpt": self.excerpt,
            "explanation": self.explanation,
        }


@dataclass
class AuditReport:
    plugin: str
    manifest: dict = field(default_factory=dict)
    signals: list[Signal] = field(default_factory=list)
    files_scanned: int = 0
    dependencies: list[str] = field(default_factory=list)
    dockerfiles: list[str] = field(default_factory=list)
    workflows: list[str] = field(default_factory=list)
    shell_scripts: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    generated_at: str = ""

    @property
    def risk_level(self) -> str:
        if not self.signals:
            return RiskLevel.LOW.value
        worst = max(_SEVERITY_ORDER[s.severity] for s in self.signals)
        # An OSINT plugin necessarily uses the network: not a risk in itself.
        if worst <= _SEVERITY_ORDER["LOW"]:
            return RiskLevel.LOW.value
        if worst == _SEVERITY_ORDER["MEDIUM"]:
            return RiskLevel.MEDIUM.value
        if worst == _SEVERITY_ORDER["HIGH"]:
            return RiskLevel.HIGH.value
        return RiskLevel.CRITICAL.value

    def has(self, code: str) -> bool:
        return any(s.code == code for s in self.signals)

    def summary(self) -> dict:
        # The manifest DECLARES expected behaviour; the scan OBSERVES it. Both
        # are merged: a plugin that delegates process spawning to
        # `app.plugins.runner` shows no `subprocess` in its own code, but its
        # manifest announces it - the report must reflect that.
        declared = str(self.manifest.get("execution_mode", "")).lower()
        declares_network = bool(self.manifest.get("network"))
        return {
            "plugin": self.plugin,
            "repository": self.manifest.get("repository"),
            "license": self.manifest.get("license"),
            "version": self.manifest.get("upstream_version"),
            "last_upstream_update": self.manifest.get("last_upstream_update"),
            "last_reviewed": self.manifest.get("last_reviewed"),
            "risk_level": self.risk_level,
            "network_access": "YES" if self.has("network") or declares_network else "NO",
            "filesystem_access": "LIMITED" if self.has("filesystem_write") else "NO",
            "subprocess": "YES" if self.has("subprocess") or "subprocess" in declared else "NO",
            "dynamic_downloads": "YES" if self.has("temp_download") else "NO",
            "privileged_operations": "YES" if self.has("privileged") else "NO",
            "docker_socket": "YES" if self.has("docker_socket") else "NO",
            "hardcoded_secrets": "YES" if self.has("hardcoded_secret") else "NO",
            "suspicious_behavior": (
                "NONE DETECTED"
                if not [s for s in self.signals if s.severity in {"HIGH", "CRITICAL"}]
                else f"{len([s for s in self.signals if s.severity in {'HIGH', 'CRITICAL'}])} signal(s)"
            ),
            "files_scanned": self.files_scanned,
            "dependencies": self.dependencies,
            "dockerfiles": self.dockerfiles,
            "github_workflows": self.workflows,
            "shell_scripts": self.shell_scripts,
            "signals": [s.as_dict() for s in self.signals],
            "errors": self.errors,
            "generated_at": self.generated_at,
            "disclaimer": (
                "Indicative static analysis. It does not guarantee the absence of "
                "malicious code. Manual review and sandboxed execution remain "
                "mandatory before activation."
            ),
        }


def audit_path(path: Path, plugin_name: str) -> AuditReport:
    """Analyse a directory (in-house plugin or a copy of a third-party tool)."""
    report = AuditReport(
        plugin=plugin_name, generated_at=datetime.now(UTC).isoformat()
    )

    manifest_file = path / "manifest.json"
    if manifest_file.exists():
        try:
            report.manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            report.errors.append(f"manifest.json is unreadable: {exc}")
    else:
        report.errors.append("manifest.json missing: provenance undeclared")

    if not path.exists():
        report.errors.append(f"path not found: {path}")
        return report

    for file in sorted(path.rglob("*")):
        if file.is_dir() or _skipped(file):
            continue
        name = file.name.lower()
        rel = str(file.relative_to(path))

        if name in {"requirements.txt", "pyproject.toml", "setup.py", "setup.cfg"}:
            report.dependencies.extend(_read_dependencies(file))
        if name.startswith("dockerfile"):
            report.dockerfiles.append(rel)
        if ".github/workflows" in str(file).replace("\\", "/"):
            report.workflows.append(rel)
        if file.suffix in {".sh", ".bash"}:
            report.shell_scripts.append(rel)

        if file.suffix not in {".py", ".sh", ".bash", ".toml", ".cfg", ".yml", ".yaml", ""} \
                and not name.startswith("dockerfile"):
            continue

        try:
            content = file.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            report.errors.append(f"cannot read {rel}: {exc}")
            continue

        report.files_scanned += 1
        report.signals.extend(_scan_text(content, rel))
        if file.suffix == ".py":
            report.signals.extend(_scan_python_ast(content, rel, report))

    report.dependencies = sorted(set(report.dependencies))
    return report


def audit_plugin(plugin_name: str) -> AuditReport:
    """Audit an in-house plugin plus the upstream path declared in its manifest."""
    plugin_dir = PLUGINS_DIR / plugin_name
    report = audit_path(plugin_dir, plugin_name)

    vendor = report.manifest.get("vendor_path")
    if vendor:
        vendor_path = Path(vendor)
        if not vendor_path.is_absolute():
            vendor_path = plugin_dir / vendor
        if vendor_path.exists():
            upstream = audit_path(vendor_path, f"{plugin_name}:upstream")
            report.signals.extend(upstream.signals)
            report.files_scanned += upstream.files_scanned
            report.dependencies.extend(upstream.dependencies)
            report.dockerfiles.extend(upstream.dockerfiles)
            report.workflows.extend(upstream.workflows)
            report.shell_scripts.extend(upstream.shell_scripts)
        else:
            report.errors.append(
                f"vendor_path declared but missing ({vendor_path}): "
                "the upstream tool was not analysed locally"
            )
    return report


def audit_all() -> list[AuditReport]:
    reports = []
    for child in sorted(PLUGINS_DIR.iterdir()):
        if child.is_dir() and (child / "plugin.py").exists():
            reports.append(audit_plugin(child.name))
    return reports


# ---------------------------------------------------------------- internals


def _skipped(file: Path) -> bool:
    parts = set(file.parts)
    return bool(
        parts & {"__pycache__", ".git", "node_modules", ".venv", "venv", "dist"}
    )


def _scan_text(content: str, rel: str) -> list[Signal]:
    signals: list[Signal] = []
    lines = content.splitlines()
    for code, pattern, severity, explanation in PATTERNS:
        for match in re.finditer(pattern, content):
            line_no = content[: match.start()].count("\n") + 1
            excerpt = lines[line_no - 1].strip()[:200] if line_no <= len(lines) else ""
            if excerpt.startswith("#") or excerpt.startswith('"""'):
                continue  # comment or docstring: not a real signal
            signals.append(Signal(code, severity, rel, line_no, excerpt, explanation))
    for code, pattern in SECRET_PATTERNS:
        for match in re.finditer(pattern, content):
            line_no = content[: match.start()].count("\n") + 1
            signals.append(
                Signal(
                    "hardcoded_secret",
                    "CRITICAL",
                    rel,
                    line_no,
                    f"<{code} masque>",
                    "Possible hardcoded secret in the source",
                )
            )
    return _dedupe(signals)


def _scan_python_ast(content: str, rel: str, report: AuditReport) -> list[Signal]:
    """Confirm some signals through AST analysis (fewer false positives)."""
    try:
        tree = ast.parse(content)
    except SyntaxError as exc:
        report.errors.append(f"invalid syntax {rel}:{exc.lineno}")
        return []

    signals: list[Signal] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = (
                func.attr
                if isinstance(func, ast.Attribute)
                else func.id if isinstance(func, ast.Name) else ""
            )
            if name in {"eval", "exec", "compile"} and isinstance(func, ast.Name):
                signals.append(
                    Signal(
                        "eval_exec",
                        "HIGH",
                        rel,
                        node.lineno,
                        f"{name}(...)",
                        "Executes dynamically built code (AST-confirmed)",
                    )
                )
            for kw in node.keywords or []:
                if (
                    kw.arg == "shell"
                    and isinstance(kw.value, ast.Constant)
                    and kw.value.value is True
                ):
                    signals.append(
                        Signal(
                            "shell_true",
                            "HIGH",
                            rel,
                            node.lineno,
                            "shell=True",
                            "subprocess with shell=True (AST-confirmed)",
                        )
                    )
    return _dedupe(signals)


def _read_dependencies(file: Path) -> list[str]:
    try:
        content = file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    deps: list[str] = []
    if file.name == "requirements.txt":
        for line in content.splitlines():
            line = line.strip()
            if line and not line.startswith(("#", "-")):
                deps.append(line)
    else:
        for match in re.finditer(r"['\"]([A-Za-z0-9_.\-]+)\s*[><=~!]{1,2}[^'\"]*['\"]", content):
            deps.append(match.group(0).strip("'\""))
    return deps


def _dedupe(signals: list[Signal]) -> list[Signal]:
    seen: set[tuple] = set()
    out: list[Signal] = []
    for signal in signals:
        key = (signal.code, signal.file, signal.line)
        if key not in seen:
            seen.add(key)
            out.append(signal)
    return out


def render_text_report(report: AuditReport) -> str:
    """Text rendering of the report, as expected by `osint plugin audit`."""
    s = report.summary()
    lines = [
        "Plugin Security Report",
        "",
        f"Plugin:              {s['plugin']}",
        f"Repository:          {s['repository'] or '-'}",
        f"License:             {s['license'] or '-'}",
        f"Version:             {s['version'] or '-'}",
        f"Last update:         {s['last_upstream_update'] or '-'}",
        f"Last reviewed:       {s['last_reviewed'] or '-'}",
        "",
        f"Risk level:          {s['risk_level']}",
        "",
        f"Network access:      {s['network_access']}",
        f"Filesystem access:   {s['filesystem_access']}",
        f"Subprocess:          {s['subprocess']}",
        f"Dynamic downloads:   {s['dynamic_downloads']}",
        f"Privileged ops:      {s['privileged_operations']}",
        f"Docker socket:       {s['docker_socket']}",
        f"Hardcoded secrets:   {s['hardcoded_secrets']}",
        f"Suspicious behavior: {s['suspicious_behavior']}",
        "",
        f"Files scanned:       {s['files_scanned']}",
        f"Dependencies:        {len(s['dependencies'])}",
    ]
    high = [x for x in report.signals if x.severity in {"HIGH", "CRITICAL"}]
    if high:
        lines += ["", "HIGH/CRITICAL signals:"]
        for signal in high[:40]:
            lines.append(
                f"  [{signal.severity}] {signal.file}:{signal.line} "
                f"{signal.code} - {signal.explanation}"
            )
    if report.errors:
        lines += ["", "Warnings:"] + [f"  - {e}" for e in report.errors]
    lines += ["", s["disclaimer"]]
    return "\n".join(lines)
