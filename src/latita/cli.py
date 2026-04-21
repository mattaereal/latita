from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import typer

from . import capsules as caps_mod
from .config import get_config, list_capsules, list_latita_templates, load_latita_template
from .operations import (
    apply_capsule_live,
    bootstrap_host,
    connect_instance,
    create_instance,
    destroy_instance,
    doctor,
    init_base,
    list_instances,
    revive_instance,
    scan_instances,
    ssh_instance,
    start_instance,
    stop_instance,
)
from .prompts import interactive_create
from .ui import console

app = typer.Typer(
    help="Latita - Ephemeral libvirt/QEMU lab manager with capsules",
    invoke_without_command=True,
)

# Sub-typer for capsules
capsule_app = typer.Typer(help="Manage capsules")
app.add_typer(capsule_app, name="capsule")

# Sub-typer for templates
template_app = typer.Typer(help="Manage templates")
app.add_typer(template_app, name="template")


@app.callback(invoke_without_command=True)
def callback(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        console.print("[bold]Latita[/bold] - Ephemeral libvirt/QEMU lab manager")
        console.print("Run 'latita --help' for commands or 'latita menu' for interactive mode.")
        raise typer.Exit()


@app.command(name="menu")
def menu_cmd() -> None:
    """Interactive menu for VM management."""
    get_config().ensure_dirs()
    while True:
        choices = [
            "create",
            "list",
            "start",
            "stop",
            "destroy",
            "ssh",
            "connect",
            "capsule apply",
            "bootstrap",
            "doctor",
            "quit",
        ]
        try:
            from .prompts import ask_select
            choice = ask_select("Menu", choices, default="list")
        except Exception:
            break
        if choice == "quit":
            break
        if choice == "create":
            _interactive_create()
        elif choice == "list":
            list_instances()
        elif choice == "start":
            name = input("VM name: ").strip()
            if name:
                start_instance(name)
        elif choice == "stop":
            name = input("VM name: ").strip()
            if name:
                stop_instance(name)
        elif choice == "destroy":
            name = input("VM name: ").strip()
            if name and typer.confirm(f"Destroy {name}?", default=False):
                destroy_instance(name)
        elif choice == "ssh":
            name = input("VM name: ").strip()
            if name:
                ssh_instance(name)
        elif choice == "connect":
            name = input("VM name: ").strip()
            if name:
                connect_instance(name)
        elif choice == "capsule apply":
            vm = input("VM name: ").strip()
            cap = input("Capsule name: ").strip()
            if vm and cap:
                apply_capsule_live(vm, cap)
        elif choice == "bootstrap":
            bootstrap_host()
        elif choice == "doctor":
            doctor()


def _interactive_create() -> None:
    try:
        recipe = interactive_create()
    except typer.Abort:
        console.print("Aborted.", style="yellow")
        return
    template_name = recipe["profile"]
    if template_name not in list_latita_templates():
        console.print(f"Template '{template_name}' not found. Creating with defaults...", style="yellow")
    create_instance(
        template_name=template_name,
        name=recipe["name"],
        capsule_names=recipe.get("capsules", []),
        overrides=recipe,
    )


@app.command(name="create")
def create_cmd(
    template: str = typer.Argument(..., help="Template name (e.g. headless, desktop)"),
    name: Optional[str] = typer.Option(None, "--name", "-n", help="VM name"),
    capsule: list[str] = typer.Option([], "--capsule", "-c", help="Capsule to apply (repeatable)"),
    ephemeral: bool = typer.Option(False, "--ephemeral", "-e", help="Destroy on stop"),
    transient: bool = typer.Option(False, "--transient", "-t", help="Libvirt transient"),
) -> None:
    """Create a VM from a template."""
    overrides: dict[str, Any] = {}
    if ephemeral:
        overrides.setdefault("ephemeral", {})["destroy_on_stop"] = True
    if transient:
        overrides.setdefault("ephemeral", {})["transient"] = True
    create_instance(template, name=name, capsule_names=capsule or None, overrides=overrides)


@app.command(name="start")
def start_cmd(name: str) -> None:
    """Start a VM."""
    start_instance(name)


@app.command(name="stop")
def stop_cmd(name: str) -> None:
    """Stop a VM. Ephemeral VMs are destroyed."""
    stop_instance(name)


@app.command(name="destroy")
def destroy_cmd(
    name: str,
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation"),
) -> None:
    """Destroy a VM and shred its overlay."""
    if not force:
        if not typer.confirm(f"Destroy VM '{name}' and shred its disk?", default=False):
            raise typer.Abort()
    destroy_instance(name)


@app.command(name="revive")
def revive_cmd(name: str) -> None:
    """Revive a stored VM (re-create libvirt domain from saved metadata)."""
    revive_instance(name)


@app.command(name="ssh")
def ssh_cmd(
    name: str,
    command: Optional[str] = typer.Argument(None, help="Optional command to run"),
) -> None:
    """SSH into a VM."""
    ssh_instance(name, command=command)


@app.command(name="connect")
def connect_cmd(name: str) -> None:
    """Connect to a VM (SPICE for desktop, SSH for headless)."""
    connect_instance(name)


@app.command(name="list")
def list_cmd() -> None:
    """List all VMs."""
    list_instances()


@app.command(name="bootstrap")
def bootstrap_cmd() -> None:
    """Bootstrap the host (networks, keys, base image)."""
    bootstrap_host()


@app.command(name="init-base")
def init_base_cmd(
    name: Optional[str] = typer.Option(None, "--name", "-n"),
    url: Optional[str] = typer.Option(None, "--url", "-u"),
) -> None:
    """Download a base cloud image."""
    init_base(name, url)


@app.command(name="doctor")
def doctor_cmd() -> None:
    """Check host dependencies."""
    doctor()


# ---------------------------------------------------------------------------
# Capsule commands
# ---------------------------------------------------------------------------

@capsule_app.command(name="list")
def capsule_list_cmd() -> None:
    """List available capsules."""
    all_caps = list_capsules()
    if not all_caps:
        console.print("No capsules found", style="yellow")
        return
    caps_mod.format_capsule_table(all_caps)


@capsule_app.command(name="apply")
def capsule_apply_cmd(
    vm: str = typer.Argument(..., help="VM name"),
    capsule: str = typer.Argument(..., help="Capsule name"),
) -> None:
    """Apply a capsule to a running VM via SSH."""
    apply_capsule_live(vm, capsule)


# ---------------------------------------------------------------------------
# Template commands
# ---------------------------------------------------------------------------

@template_app.command(name="list")
def template_list_cmd() -> None:
    """List available .latita templates."""
    templates = list_latita_templates()
    if not templates:
        console.print("No templates found", style="yellow")
        return
    from rich.table import Table
    table = Table(title="Templates")
    table.add_column("Name")
    table.add_column("Profile")
    table.add_column("Description")
    for name, data in templates.items():
        table.add_row(
            name,
            str(data.get("profile", "-")),
            str(data.get("description", "")).strip() or "-",
        )
    console.print(table)


@template_app.command(name="show")
def template_show_cmd(name: str) -> None:
    """Show a template's contents."""
    data = load_latita_template(name)
    console.print(f"[bold]{name}.latita[/bold]")
    console.print(data)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    app()
