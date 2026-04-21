from __future__ import annotations

from typing import Any

import typer

try:
    import questionary
except ImportError:
    questionary = None

from .config import get_config, list_capsules
from .libvirt import detect_default_uplink
from .ui import console
from .utils import (
    create_host_key,
    create_lab_key,
    default_host_pubkey,
    hash_password_interactive,
    host_key_exists,
    lab_key_exists,
    validate_cpus,
    validate_disk_size,
    validate_ip,
    validate_memory,
)


def _ensure_questionary() -> None:
    if questionary is None:
        raise typer.BadParameter("questionary is required for interactive mode. Install with 'uv sync'")


def ask_text(message: str, default: str | None = None) -> str:
    _ensure_questionary()
    res = questionary.text(message, default=default or "").ask()
    if res is None:
        raise typer.Abort()
    return res.strip()


def ask_select(message: str, choices: list[str], default: str | None = None) -> str:
    _ensure_questionary()
    valid_default = default if default in choices else (choices[0] if choices else None)
    res = questionary.select(message, choices=choices, default=valid_default).ask()
    if res is None:
        raise typer.Abort()
    return res


def ask_confirm(message: str, default: bool = True) -> bool:
    _ensure_questionary()
    res = questionary.confirm(message, default=default).ask()
    if res is None:
        raise typer.Abort()
    return bool(res)


def ask_checkbox(message: str, choices: list[str], default: list[str] | None = None) -> list[str]:
    _ensure_questionary()
    valid_default = [d for d in (default or []) if d in choices] if default else None
    res = questionary.checkbox(message, choices=choices, default=valid_default or []).ask()
    if res is None:
        raise typer.Abort()
    return list(res)


def ask_password(message: str) -> str:
    _ensure_questionary()
    res = questionary.password(message).ask()
    if res is None:
        raise typer.Abort()
    return res.strip()


def interactive_create() -> dict[str, Any]:
    console.print("\n[bold]Create a new VM[/bold]\n")
    cfg = get_config()

    profile = ask_select("Profile", ["headless", "desktop"], default="headless")

    name = ask_text("VM name", _suggest_name(profile))
    validate_name(name)

    mgmt_ip = ask_text("Management IP", "10.31.0.10")
    validate_ip(mgmt_ip)

    net_mode = ask_select("Network mode", ["nat", "direct", "auto"], default="nat")
    nat_network = ""
    uplink = ""
    if net_mode == "nat":
        nat_network = ask_text("NAT network", "default")
    elif net_mode == "direct":
        uplink = ask_text("Uplink interface", detect_default_uplink() or "")
    else:
        if ask_confirm("Set NAT fallback network?", True):
            nat_network = ask_text("NAT fallback network", "default")

    cpus = validate_cpus(ask_text("vCPUs", "2"))
    memory = validate_memory(ask_text("Memory (MiB)", "4096"))
    disk_size = validate_disk_size(ask_text("Disk size", "20G"))
    guest_user = ask_text("Guest user", "dev")

    transient = ask_confirm("Transient (libvirt transient)?", profile == "headless")
    destroy_on_stop = ask_confirm("Destroy on stop (ephemeral)?", False)
    max_runs = ask_text("Max runs (empty for no limit)", "")
    expires = ask_text("Expires after hours (empty for no limit)", "")

    # Security
    selinux = ask_confirm("Enable SELinux hardening?", True)
    no_agent = ask_confirm("Disable qemu-guest-agent?", True)
    restrict_net = ask_confirm("Restrict outbound network?", False)
    allow_hosts = []
    if restrict_net:
        hosts_input = ask_text("Allowed hosts (comma separated, empty for none)", "")
        allow_hosts = [h.strip() for h in hosts_input.split(",") if h.strip()]

    # Capsules
    available_capsules = list(list_capsules().keys())
    selected_capsules: list[str] = []
    if available_capsules:
        selected_capsules = ask_checkbox("Select capsules", available_capsules)

    # Desktop password
    login_hash = ""
    if profile == "desktop":
        login_hash = hash_password_interactive()

    recipe = {
        "profile": profile,
        "name": name,
        "network": {
            "mode": net_mode,
            "nat_network": nat_network,
            "uplink": uplink or None,
            "mgmt_ip": mgmt_ip,
            "mgmt_prefix": "24",
        },
        "cpus": cpus,
        "memory": memory,
        "disk_size": disk_size,
        "guest_user": guest_user,
        "passwordless_sudo": True,
        "ephemeral": {
            "transient": transient,
            "destroy_on_stop": destroy_on_stop,
            "max_runs": int(max_runs) if max_runs.strip() else None,
            "expires_after_hours": int(expires) if expires.strip() else None,
        },
        "security": {
            "selinux": selinux,
            "no_guest_agent": no_agent,
            "restrict_network": restrict_net,
            "allow_hosts": allow_hosts,
        },
        "capsules": selected_capsules,
        "provision": {
            "packages": [],
            "write_files": [],
            "root_commands": [],
            "user_commands": [],
        },
    }

    if profile == "desktop":
        recipe["login_hash"] = login_hash

    return recipe


def _suggest_name(profile: str) -> str:
    prefix = "desktop-" if profile == "desktop" else "vm-"
    return f"{prefix}001"


def validate_name(value: str) -> None:
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_")
    if not value or any(c not in allowed for c in value):
        raise typer.BadParameter("name must contain only letters, numbers, dash, underscore")
