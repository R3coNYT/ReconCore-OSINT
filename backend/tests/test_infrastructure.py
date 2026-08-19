"""Infrastructure invariants: isolation must not regress silently."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "docker-compose.yml"

#: Patterns that would give a container root-equivalent power on the host.
FORBIDDEN = ("/var/run/docker.sock", "--privileged", "privileged: true", "-v /:/")

TOOL_SERVICES = (
    "worker-sherlock",
    "worker-holehe",
    "worker-phoneinfoga",
    "worker-toutatis",
)


@pytest.fixture(scope="module")
def compose_text() -> str:
    if not COMPOSE.exists():
        pytest.skip("docker-compose.yml is missing")
    return COMPOSE.read_text(encoding="utf-8")


@pytest.mark.parametrize("pattern", FORBIDDEN)
def test_no_dangerous_docker_directive(compose_text: str, pattern: str) -> None:
    assert pattern not in compose_text, (
        f"Forbidden directive in docker-compose.yml: {pattern}"
    )


def test_tool_workers_have_no_database_credentials(compose_text: str) -> None:
    """The tool workers' shared environment block must expose nothing about the
    database: that is what guarantees a third-party tool cannot reach it."""
    match = re.search(r"x-tool-env: &tool-env\n(.*?)\n\nservices:", compose_text, re.S)
    assert match, "x-tool-env block not found"
    # Comments may mention those names to explain why they are absent.
    block = "\n".join(
        line for line in match.group(1).splitlines() if not line.strip().startswith("#")
    )
    for forbidden in ("POSTGRES_", "DATABASE_URL", "SECRETS_ENCRYPTION_KEY"):
        assert forbidden not in block, (
            f"{forbidden} must not be passed to tool workers"
        )


def test_tool_workers_are_not_on_the_data_network(compose_text: str) -> None:
    yaml = pytest.importorskip("yaml")
    data = yaml.safe_load(compose_text)
    for name in TOOL_SERVICES:
        service = data["services"].get(name)
        assert service is not None, f"service {name} is missing"
        assert "data" not in (service.get("networks") or []), (
            f"{name} must not sit on the `data` network (PostgreSQL access)"
        )


def test_tool_workers_are_hardened(compose_text: str) -> None:
    yaml = pytest.importorskip("yaml")
    data = yaml.safe_load(compose_text)
    for name in TOOL_SERVICES:
        service = data["services"][name]
        assert service.get("read_only") is True, f"{name} must set read_only"
        assert service.get("cap_drop") == ["ALL"], f"{name} must drop all capabilities"
        assert "no-new-privileges:true" in service.get("security_opt", [])
        assert service.get("pids_limit"), f"{name} must limit its processes"
        assert service.get("mem_limit"), f"{name} must limit its memory"


def test_env_example_has_no_real_secret() -> None:
    example = ROOT / ".env.example"
    if not example.exists():
        pytest.skip(".env.example is missing")
    content = example.read_text(encoding="utf-8")
    assert "CHANGE_ME" in content, "sensitive values must stay placeholders"
    for line in content.splitlines():
        if line.startswith("SECRET_KEY=") or line.startswith("SECRETS_ENCRYPTION_KEY="):
            assert "CHANGE_ME" in line, f"real value committed: {line.split('=')[0]}"


def test_toutatis_is_opt_in_everywhere(compose_text: str) -> None:
    yaml = pytest.importorskip("yaml")
    data = yaml.safe_load(compose_text)
    assert data["services"]["worker-toutatis"].get("profiles") == ["toutatis"], (
        "the Toutatis worker must only start under an explicit profile"
    )
    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert "TOUTATIS_ENABLED=false" in example
