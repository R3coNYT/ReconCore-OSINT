"""Administration CLI: `osint <command>`.

Examples:
    osint db init
    osint user create --email admin@example.org --role ADMIN
    osint plugin list
    osint plugin audit sherlock
    osint plugin enable sherlock
    osint plugin secret set toutatis sessionid
    osint retention apply
"""
from __future__ import annotations

import getpass
import json
import sys
from datetime import UTC

import typer
from rich.console import Console
from rich.table import Table

from app.core.logging import setup_logging

setup_logging()
console = Console()

app = typer.Typer(help="ReconCore OSINT administration", no_args_is_help=True)
db_app = typer.Typer(help="Database", no_args_is_help=True)
user_app = typer.Typer(help="User accounts", no_args_is_help=True)
plugin_app = typer.Typer(help="OSINT plugins", no_args_is_help=True)
secret_app = typer.Typer(help="Plugin secrets", no_args_is_help=True)
retention_app = typer.Typer(help="Retention policy", no_args_is_help=True)

app.add_typer(db_app, name="db")
app.add_typer(user_app, name="user")
app.add_typer(plugin_app, name="plugin")
plugin_app.add_typer(secret_app, name="secret")
app.add_typer(retention_app, name="retention")


# ---------------------------------------------------------------------- db


@db_app.command("init")
def db_init(seed: bool = typer.Option(True, help="Insert the platform catalogue")) -> None:
    """Create the tables and seed reference data."""
    import app.models  # noqa: F401 - importing registers every table
    from app.db.base import Base
    from app.db.session import engine, session_scope
    from app.plugins import registry
    from app.services.platforms import seed_platforms

    Base.metadata.create_all(bind=engine)
    console.print("[green]Tables created / verified[/green]")

    with session_scope() as db:
        if seed:
            created = seed_platforms(db)
            console.print(f"[green]{created} platform(s) added[/green]")
        entries = registry.sync_registry(db)
        console.print(f"[green]{len(entries)} plugin(s) registered[/green]")


@db_app.command("drop")
def db_drop(
    confirm: bool = typer.Option(False, "--yes", help="Confirm dropping everything")
) -> None:
    """Drop every table. Destructive."""
    if not confirm:
        console.print("[red]Add --yes to confirm dropping every table[/red]")
        raise typer.Exit(code=1)
    import app.models  # noqa: F401 - importing registers every table
    from app.db.base import Base
    from app.db.session import engine

    Base.metadata.drop_all(bind=engine)
    console.print("[yellow]Every table has been dropped[/yellow]")


# -------------------------------------------------------------------- users


@user_app.command("create")
def user_create(
    email: str = typer.Option(..., prompt=True),
    role: str = typer.Option("ANALYST", help="ADMIN | ANALYST | READ_ONLY"),
    full_name: str = typer.Option("", help="Display name"),
    password: str = typer.Option("", help="Leave empty for a masked prompt"),
) -> None:
    """Create an account. The password is never echoed or logged."""
    from sqlalchemy import select

    from app.db.session import session_scope
    from app.models.enums import UserRole
    from app.models.user import User
    from app.security.passwords import hash_password, validate_password_strength

    if role not in {r.value for r in UserRole}:
        console.print(f"[red]Invalid role: {role}[/red]")
        raise typer.Exit(code=1)

    if not password:
        password = getpass.getpass("Password: ")
        if password != getpass.getpass("Confirm: "):
            console.print("[red]Passwords do not match[/red]")
            raise typer.Exit(code=1)

    problems = validate_password_strength(password)
    if problems:
        console.print(f"[red]Password too weak: {', '.join(problems)}[/red]")
        raise typer.Exit(code=1)

    with session_scope() as db:
        if db.execute(select(User).where(User.email == email.lower())).scalar_one_or_none():
            console.print("[red]This email already exists[/red]")
            raise typer.Exit(code=1)
        db.add(
            User(
                email=email.lower(),
                full_name=full_name or None,
                hashed_password=hash_password(password),
                role=role,
            )
        )
    console.print(f"[green]Account {email} created ({role})[/green]")


@user_app.command("list")
def user_list() -> None:
    from sqlalchemy import select

    from app.db.session import session_scope
    from app.models.user import User

    table = Table("Email", "Role", "Active", "Last login")
    with session_scope() as db:
        for user in db.execute(select(User).order_by(User.created_at)).scalars().all():
            table.add_row(
                user.email,
                user.role,
                "yes" if user.is_active else "no",
                user.last_login_at.isoformat() if user.last_login_at else "-",
            )
    console.print(table)


# ------------------------------------------------------------------ plugins


@plugin_app.command("list")
def plugin_list() -> None:
    from app.db.session import session_scope
    from app.plugins import registry

    table = Table("Plugin", "Version", "State", "Risk", "Identifiers", "Queue")
    with session_scope() as db:
        registry.sync_registry(db)
        for plugin in registry.all_plugins():
            entry = registry.get_entry(db, plugin.name)
            table.add_row(
                plugin.name,
                plugin.version,
                "[green]ENABLED[/green]" if entry and entry.enabled else "[dim]DISABLED[/dim]",
                entry.risk_level if entry else "UNKNOWN",
                ",".join(plugin.supported_identifiers),
                plugin.queue,
            )
    console.print(table)


@plugin_app.command("audit")
def plugin_audit_cmd(
    name: str = typer.Argument(..., help="Plugin name, or 'all'"),
    as_json: bool = typer.Option(False, "--json", help="JSON output"),
    save: bool = typer.Option(True, help="Store the report in the database"),
) -> None:
    """Static security audit of a plugin (a decision aid, not a guarantee)."""
    from datetime import datetime

    from app.db.session import session_scope
    from app.plugins import audit as plugin_audit
    from app.plugins import registry

    names = (
        [p.name for p in registry.all_plugins()] if name == "all" else [name]
    )
    if name != "all" and registry.get(name) is None:
        console.print(f"[red]Unknown plugin: {name}[/red]")
        raise typer.Exit(code=1)

    exit_code = 0
    for plugin_name in names:
        report = plugin_audit.audit_plugin(plugin_name)
        if as_json:
            console.print_json(json.dumps(report.summary(), ensure_ascii=False))
        else:
            console.print(plugin_audit.render_text_report(report))
            console.print("")
        if report.risk_level in {"HIGH", "CRITICAL"}:
            exit_code = 2
        if save:
            with session_scope() as db:
                registry.sync_registry(db)
                entry = registry.get_entry(db, plugin_name)
                if entry:
                    entry.risk_level = report.risk_level
                    entry.last_audit_at = datetime.now(UTC)
                    entry.audit_report = report.summary()
    raise typer.Exit(code=exit_code)


@plugin_app.command("enable")
def plugin_enable(
    name: str,
    acknowledge: bool = typer.Option(
        False, "--acknowledge-risks", help="Confirm you have read the warnings"
    ),
) -> None:
    from app.db.session import session_scope
    from app.plugins import registry

    plugin = registry.get(name)
    if plugin is None:
        console.print(f"[red]Unknown plugin: {name}[/red]")
        raise typer.Exit(code=1)
    if plugin.risk_notes and not acknowledge:
        console.print("[yellow]Warnings for this plugin:[/yellow]")
        for note in plugin.risk_notes:
            console.print(f"  - {note}")
        console.print("Run again with --acknowledge-risks to enable it.")
        raise typer.Exit(code=1)

    with session_scope() as db:
        registry.sync_registry(db)
        missing = [k for k, ok in registry.secret_status(db, plugin).items() if not ok]
        if missing:
            console.print(f"[red]Missing secrets: {', '.join(missing)}[/red]")
            raise typer.Exit(code=1)
        registry.get_entry(db, name).enabled = True
    console.print(f"[green]Plugin {name} enabled[/green]")


@plugin_app.command("disable")
def plugin_disable(name: str) -> None:
    from app.db.session import session_scope
    from app.plugins import registry

    with session_scope() as db:
        registry.sync_registry(db)
        entry = registry.get_entry(db, name)
        if entry is None:
            console.print(f"[red]Unknown plugin: {name}[/red]")
            raise typer.Exit(code=1)
        entry.enabled = False
    console.print(f"[yellow]Plugin {name} disabled[/yellow]")


@plugin_app.command("health")
def plugin_health_cmd(name: str) -> None:
    """Check tool availability (run this INSIDE the relevant worker)."""
    from app.plugins import registry

    plugin = registry.get(name)
    if plugin is None:
        console.print(f"[red]Unknown plugin: {name}[/red]")
        raise typer.Exit(code=1)
    status = plugin.check_health()
    color = "green" if status.ok else "red"
    console.print(f"[{color}]{name}: {status.message}[/{color}]")
    raise typer.Exit(code=0 if status.ok else 1)


@secret_app.command("set")
def secret_set(
    plugin: str,
    key: str,
    value: str = typer.Option("", help="Leave empty for a masked prompt"),
) -> None:
    """Set an encrypted secret. Never put an account password here."""
    from app.db.session import session_scope
    from app.plugins import registry
    from app.security.crypto import SecretsUnavailable

    target = registry.get(plugin)
    if target is None:
        console.print(f"[red]Plugin inconnu : {plugin}[/red]")
        raise typer.Exit(code=1)
    if key not in target.requires_secrets:
        console.print(
            f"[red]This plugin expects: {target.requires_secrets or 'no secret'}[/red]"
        )
        raise typer.Exit(code=1)
    if "password" in key.lower():
        console.print("[red]Storing passwords is forbidden.[/red]")
        raise typer.Exit(code=1)

    if not value:
        value = getpass.getpass(f"Value for {plugin}.{key} (hidden): ")
    if not value:
        console.print("[red]Empty value[/red]")
        raise typer.Exit(code=1)

    try:
        with session_scope() as db:
            registry.set_secret(db, plugin, key, value)
    except SecretsUnavailable as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc
    console.print(f"[green]Secret {plugin}.{key} stored (encrypted)[/green]")


@secret_app.command("list")
def secret_list(plugin: str) -> None:
    from sqlalchemy import select

    from app.db.session import session_scope
    from app.models.ops import PluginSecret

    table = Table("Plugin", "Key", "Preview", "Updated")
    with session_scope() as db:
        for record in db.execute(
            select(PluginSecret).where(PluginSecret.plugin == plugin)
        ).scalars().all():
            table.add_row(
                record.plugin, record.key, record.hint or "-", record.updated_at.isoformat()
            )
    console.print(table)


@secret_app.command("delete")
def secret_delete(plugin: str, key: str) -> None:
    from app.db.session import session_scope
    from app.plugins import registry

    with session_scope() as db:
        removed = registry.delete_secret(db, plugin, key)
    console.print(
        "[green]Secret deleted[/green]" if removed else "[yellow]Secret not found[/yellow]"
    )


# ---------------------------------------------------------------- retention


@retention_app.command("apply")
def retention_apply() -> None:
    """Apply the retention policy (permanent deletion)."""
    from app.workers.tasks import apply_retention

    result = apply_retention()
    console.print(f"[green]Retention applied: {result}[/green]")


# ---------------------------------------------------------------- one-shot


#: Plugins enabled by `osint setup` when nothing is specified. Toutatis is
#: excluded on purpose: it needs an explicit opt-in and a session cookie.
DEFAULT_PLUGINS = "sherlock,holehe,phoneinfoga,websearch"


@app.command("setup")
def setup(
    enable: str = typer.Option(
        DEFAULT_PLUGINS,
        "--enable",
        help="Comma-separated plugins to enable ('none' to enable nothing).",
    ),
    admin_email: str = typer.Option(
        "", "--admin-email", help="Defaults to FIRST_ADMIN_EMAIL."
    ),
    admin_password: str = typer.Option(
        "", "--admin-password", help="Defaults to FIRST_ADMIN_PASSWORD."
    ),
    seed: bool = typer.Option(True, help="Insert the platform catalogue"),
) -> None:
    """Create the schema, the admin account and enable plugins, in one shot.

    Idempotent: running it again on an existing installation changes nothing
    that already exists.
    """
    from sqlalchemy import select

    import app.models  # noqa: F401 - importing registers every table
    from app.core.config import settings
    from app.db.base import Base
    from app.db.session import engine, session_scope
    from app.models.enums import UserRole
    from app.models.user import User
    from app.plugins import registry
    from app.security.passwords import hash_password, validate_password_strength
    from app.services.platforms import seed_platforms

    Base.metadata.create_all(bind=engine)
    console.print("[green]Schema created / verified[/green]")

    email = (admin_email or settings.first_admin_email or "").strip().lower()
    password = admin_password or settings.first_admin_password

    with session_scope() as db:
        if seed:
            console.print(f"[green]{seed_platforms(db)} platform(s) added[/green]")
        entries = registry.sync_registry(db)
        console.print(f"[green]{len(entries)} plugin(s) registered[/green]")

        # --- administrator ---------------------------------------------------
        if not email:
            console.print("[yellow]No admin email given: account not created[/yellow]")
        elif db.execute(select(User).where(User.email == email)).scalar_one_or_none():
            console.print(f"[yellow]Admin account already exists: {email}[/yellow]")
        elif not password:
            console.print(
                "[red]No admin password given (FIRST_ADMIN_PASSWORD or "
                "--admin-password): account not created[/red]"
            )
            raise typer.Exit(code=1)
        else:
            problems = validate_password_strength(password)
            if problems:
                console.print(f"[red]Admin password too weak: {', '.join(problems)}[/red]")
                raise typer.Exit(code=1)
            db.add(
                User(
                    email=email,
                    full_name="Administrator",
                    hashed_password=hash_password(password),
                    role=UserRole.ADMIN.value,
                )
            )
            console.print(f"[green]Administrator account created: {email}[/green]")

        # --- plugins ---------------------------------------------------------
        wanted = [p.strip() for p in enable.split(",") if p.strip() and p.strip() != "none"]
        for name in wanted:
            plugin = registry.get(name)
            if plugin is None:
                console.print(f"[red]Unknown plugin ignored: {name}[/red]")
                continue
            if name == "toutatis" and not settings.toutatis_enabled:
                console.print(
                    "[yellow]toutatis skipped: set TOUTATIS_ENABLED=true and "
                    "configure its sessionid secret first[/yellow]"
                )
                continue
            missing = [k for k, ok in registry.secret_status(db, plugin).items() if not ok]
            if missing:
                console.print(
                    f"[yellow]{name} skipped: missing secret(s) {', '.join(missing)}[/yellow]"
                )
                continue
            registry.get_entry(db, name).enabled = True
            console.print(f"[green]Plugin enabled: {name}[/green]")

    console.print("[green]Setup complete.[/green]")


@app.command("version")
def version() -> None:
    console.print("ReconCore OSINT 1.0.0")


def main() -> None:
    try:
        app()
    except KeyboardInterrupt:  # pragma: no cover
        console.print("\n[yellow]Interrupted[/yellow]")
        sys.exit(130)


if __name__ == "__main__":
    main()
