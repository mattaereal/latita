from __future__ import annotations

from typing import Any, Callable

import typer

try:
    import questionary
except ImportError:
    questionary = None

from .config import get_config, list_capsules, write_yaml
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


# ---------------------------------------------------------------------------
# Navigation exceptions
# ---------------------------------------------------------------------------

class MenuBack(Exception):
    """User wants to go back one step in a wizard."""


class MenuCancel(Exception):
    """User wants to cancel the current wizard and return to menu."""


# ---------------------------------------------------------------------------
# Questionary helpers with back/cancel support
# ---------------------------------------------------------------------------

def _ensure_questionary() -> None:
    if questionary is None:
        raise typer.BadParameter("questionary is required for interactive mode. Install with 'uv sync'")


def ask_text(message: str, default: str = "", *, allow_back: bool = False) -> str:
    _ensure_questionary()
    hint = ""
    if allow_back:
        hint = "  (Enter alone to go back)"
    res = questionary.text(f"{message}{hint}", default=default).ask()  # type: ignore[misc]
    if res is None:
        raise MenuCancel()
    stripped = res.strip()
    if allow_back and not stripped:
        raise MenuBack()
    return stripped


def ask_select(
    message: str,
    choices: list[str],
    default: str | None = None,
    *,
    allow_back: bool = False,
) -> str:
    _ensure_questionary()
    full_choices: list[str] = []
    if allow_back:
        full_choices = ["← Cancel", "← Back"] + list(choices)
    else:
        full_choices = ["← Cancel"] + list(choices)

    valid_default = None
    if default:
        if default in full_choices:
            valid_default = default
        elif default in choices:
            valid_default = default
        else:
            valid_default = None

    res = questionary.select(message, choices=full_choices, default=valid_default).ask()  # type: ignore[misc]
    if res is None:
        raise MenuCancel()
    if res == "← Cancel":
        raise MenuCancel()
    if res == "← Back":
        raise MenuBack()
    return res


def ask_confirm(message: str, default: bool = True, *, allow_back: bool = False) -> bool:
    if allow_back:
        choices = ["Yes", "No"]
        default_str = "Yes" if default else "No"
        result = ask_select(message, choices, default=default_str, allow_back=True)
        return result == "Yes"
    _ensure_questionary()
    res = questionary.confirm(message, default=default).ask()  # type: ignore[misc]
    if res is None:
        raise MenuCancel()
    return bool(res)


def ask_checkbox(
    message: str,
    choices: list[str],
    default: list[str] | None = None,
    *,
    allow_back: bool = False,
) -> list[str]:
    _ensure_questionary()
    full_choices: list[str] = []
    if allow_back:
        full_choices = ["← Cancel", "← Back"] + list(choices)
    else:
        full_choices = ["← Cancel"] + list(choices)

    valid_default = []
    if default:
        valid_default = [d for d in default if d in full_choices]

    res = questionary.checkbox(message, choices=full_choices, default=valid_default).ask()  # type: ignore[misc]
    if res is None:
        raise MenuCancel()
    if "← Cancel" in res:
        raise MenuCancel()
    if "← Back" in res:
        raise MenuBack()
    # Filter out any navigation choices that might have been pre-selected
    return [r for r in res if r not in ("← Cancel", "← Back")]


def ask_password(message: str, *, allow_back: bool = False) -> str:
    _ensure_questionary()
    hint = ""
    if allow_back:
        hint = "  (type ← to go back, empty to cancel)"
    res = questionary.password(f"{message}{hint}").ask()  # type: ignore[misc]
    if res is None:
        raise MenuCancel()
    stripped = res.strip()
    if allow_back and stripped == "←":
        raise MenuBack()
    return stripped


# ---------------------------------------------------------------------------
# Wizard engine — reversible step execution
# ---------------------------------------------------------------------------

WizardStep = tuple[str, Callable[[dict[str, Any], bool], Any]]


def _run_wizard(steps: list[WizardStep]) -> dict[str, Any]:
    """Execute a list of wizard steps with back/cancel support.

    Each step is (key, callable). The callable receives the accumulated state dict
    and a `allow_back` bool (True for all steps after the first). It returns the
    value to store under `key`. On MenuBack we rewind one step; on MenuCancel we
    propagate. If the user goes back from step 0, MenuCancel is raised.
    """
    state: dict[str, Any] = {}
    step_idx = 0
    while step_idx < len(steps):
        key, fn = steps[step_idx]
        allow_back = step_idx > 0
        try:
            value = fn(state, allow_back)
            state[key] = value
            step_idx += 1
        except MenuBack:
            if step_idx > 0:
                step_idx -= 1
            else:
                raise MenuCancel() from None
    return state


# ---------------------------------------------------------------------------
# VM pickers for interactive menu
# ---------------------------------------------------------------------------

def _pick_vm(prompt: str, state_filter: str | None = None) -> str | None:
    """Interactive VM picker. Returns None if no VMs or user cancels."""
    _ensure_questionary()
    from .operations import scan_instances

    entries = scan_instances()
    if state_filter:
        entries = [e for e in entries if e["status"] == state_filter]
    if not entries:
        console.print(f"No {state_filter or ''} VMs found", style="yellow")
        return None
    choices = [f"{e['name']} ({e['status']})" for e in entries]
    choices.insert(0, "← Cancel")
    res = questionary.select(prompt, choices=choices).ask()  # type: ignore[misc]
    if res is None or res == "← Cancel":
        return None
    return res.split(" (")[0]


def _pick_running_vm(prompt: str) -> str | None:
    return _pick_vm(prompt, state_filter="running")


def _pick_stopped_vm(prompt: str) -> str | None:
    _ensure_questionary()
    from .operations import scan_instances

    entries = scan_instances()
    entries = [e for e in entries if e["status"] != "running"]
    if not entries:
        console.print("No stopped VMs found", style="yellow")
        return None
    choices = [f"{e['name']} ({e['status']})" for e in entries]
    choices.insert(0, "← Cancel")
    res = questionary.select(prompt, choices=choices).ask()  # type: ignore[misc]
    if res is None or res == "← Cancel":
        return None
    return res.split(" (")[0]


def _pick_capsule(prompt: str) -> str | None:
    """Interactive capsule picker."""
    _ensure_questionary()
    from .config import list_capsules

    caps = list(list_capsules().keys())
    if not caps:
        console.print("No capsules found", style="yellow")
        return None
    choices = ["← Cancel"] + caps
    res = questionary.select(prompt, choices=choices).ask()  # type: ignore[misc]
    if res is None or res == "← Cancel":
        return None
    return res


def prompt_download_base_image() -> bool:
    """Prompt user to download a base image. Returns True if downloaded."""
    _ensure_questionary()
    from .config import BASE_IMAGES
    from .operations import init_base

    choices = list(BASE_IMAGES.keys()) + ["← Cancel"]
    choice = questionary.select(
        "Base image missing. Choose one to download:",
        choices=choices,
    ).ask()  # type: ignore[misc]
    if choice is None or choice == "← Cancel":
        return False
    info = BASE_IMAGES[choice]
    init_base(info["filename"], info["url"])
    return True


# ---------------------------------------------------------------------------
# Hierarchical interactive menu (questionary, in-place, no trail)
# ---------------------------------------------------------------------------

MenuAction = Callable[[], bool]


def _submenu(title: str, actions: dict[str, MenuAction], parent: str | None = None) -> bool:
    """Show an interactive sub-menu with questionary. Return True to go back."""
    _ensure_questionary()
    labels = list(actions.keys())
    choices = ["← Back"] + labels
    subtitle = f"{parent} > {title}" if parent else title
    while True:
        result = questionary.select(subtitle, choices=choices).ask()  # type: ignore[misc]
        if result is None:
            return True  # Ctrl-C / escape = go back
        if result == "← Back":
            return True
        try:
            actions[result]()
        except (MenuBack, MenuCancel):
            # Wizard was cancelled or backed out from step 0 — return to this submenu
            pass
        except typer.Abort:
            console.print("[yellow]Aborted.[/yellow]")
        except Exception as exc:
            console.print(f"[red]Error: {exc}[/red]")
        # After any action, return to the same submenu automatically
        # The user can pick "← Back" when done


def menu_loop(
    on_create: Callable[[], None],
    on_run: Callable[[], None],
    on_list: Callable[[], None],
    on_start: Callable[[], None],
    on_stop: Callable[[], None],
    on_destroy: Callable[[], None],
    on_ssh: Callable[[], None],
    on_connect: Callable[[], None],
    on_capsule_apply: Callable[[], None],
    on_bootstrap: Callable[[], None],
    on_doctor: Callable[[], None],
) -> None:
    """Run the hierarchical interactive menu."""
    _ensure_questionary()
    cfg = get_config()
    first_run = not cfg.root_marker_path.exists()

    vm_actions: dict[str, MenuAction] = {
        "Create VM": lambda: (on_create(), True)[1],
        "Run one-shot VM": lambda: (on_run(), True)[1],
        "List VMs": lambda: (on_list(), True)[1],
    }
    lifecycle_actions: dict[str, MenuAction] = {
        "Start VM": lambda: (on_start(), True)[1],
        "Stop VM": lambda: (on_stop(), True)[1],
        "Destroy VM": lambda: (on_destroy(), True)[1],
    }
    connect_actions: dict[str, MenuAction] = {
        "SSH into VM": lambda: (on_ssh(), True)[1],
        "Connect (SPICE/desktop)": lambda: (on_connect(), True)[1],
    }
    extras_actions: dict[str, MenuAction] = {
        "Apply capsule": lambda: (on_capsule_apply(), True)[1],
    }
    setup_actions: dict[str, MenuAction] = {
        "Bootstrap host": lambda: (on_bootstrap(), True)[1],
        "Doctor check": lambda: (on_doctor(), True)[1],
    }

    top_labels = ["VMs", "Lifecycle", "Connect", "Extras"]
    top_groups = [vm_actions, lifecycle_actions, connect_actions, extras_actions]

    if first_run:
        top_labels.append("Setup")
        top_groups.append(setup_actions)

    from questionary import Choice
    choices: list[Any] = [Choice("← Quit", value="← Quit")]
    for i, label in enumerate(top_labels, 1):
        choices.append(Choice(label, value=label, shortcut_key=str(i)))
    while True:
        result = questionary.select("Latita Menu", choices=choices).ask()  # type: ignore[misc]
        if result is None or result == "← Quit":
            break
        if result in top_labels:
            idx = top_labels.index(result)
            _submenu(result, top_groups[idx], parent="Latita Menu")


# ---------------------------------------------------------------------------
# Tiered interactive creation wizards
# ---------------------------------------------------------------------------

def _suggest_name(profile: str) -> str:
    prefix = "desktop-" if profile == "desktop" else "vm-"
    return f"{prefix}001"


def validate_name(value: str) -> None:
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_")
    if not value or any(c not in allowed for c in value):
        raise typer.BadParameter("name must contain only letters, numbers, dash, underscore")


def interactive_create_simple() -> dict[str, Any]:
    """Minimal wizard: profile + name only. Template provides the rest."""
    console.print("\n[bold]Create a VM[/bold]  (templates define everything else)\n")

    def _step_profile(state: dict[str, Any], back: bool) -> str:
        return ask_select("Profile", ["headless", "desktop"], default="headless", allow_back=back)

    def _step_name(state: dict[str, Any], back: bool) -> str:
        profile = state.get("profile", "headless")
        default_name = _suggest_name(profile)
        name = ask_text("VM name", default=default_name, allow_back=back)
        validate_name(name)
        return name

    def _step_network(state: dict[str, Any], back: bool) -> str:
        enable = ask_confirm("Enable networking?", default=True, allow_back=back)
        return "nat" if enable else "isolated"

    state = _run_wizard([
        ("profile", _step_profile),
        ("name", _step_name),
        ("net_mode", _step_network),
    ])

    return {
        "profile": state["profile"],
        "template_name": state["profile"],
        "name": state["name"],
        "network": {
            "mode": state["net_mode"],
            "nat_network": "default" if state["net_mode"] == "nat" else "",
        },
    }


def interactive_create_advanced() -> dict[str, Any]:
    """Simple + resource overrides and capsules."""
    # Start with simple wizard
    simple = interactive_create_simple()

    # Override simple's cancel/back into our own wizard flow
    # We append extra steps to the simple result
    def _step_cpus(state: dict[str, Any], back: bool) -> str:
        raw = ask_text("vCPUs (empty for default)", default="", allow_back=back)
        if raw:
            return validate_cpus(raw)
        return None  # type: ignore[return-value]

    def _step_memory(state: dict[str, Any], back: bool) -> int | None:
        raw = ask_text("Memory MiB (empty for default)", default="", allow_back=back)
        if raw:
            return validate_memory(raw)
        return None

    def _step_disk(state: dict[str, Any], back: bool) -> str | None:
        raw = ask_text("Disk size (empty for default)", default="", allow_back=back)
        if raw:
            return validate_disk_size(raw)
        return None

    def _step_capsules(state: dict[str, Any], back: bool) -> list[str]:
        available = list(list_capsules().keys())
        if not available:
            return []
        return ask_checkbox("Select capsules", available, allow_back=back)

    def _step_transient(state: dict[str, Any], back: bool) -> bool:
        profile = state.get("profile", "headless")
        return ask_confirm("Transient (auto-removed on shutdown)?", default=profile == "headless", allow_back=back)

    def _step_destroy(state: dict[str, Any], back: bool) -> bool:
        return ask_confirm("Destroy on stop?", default=False, allow_back=back)

    extra_state = _run_wizard([
        ("cpus", _step_cpus),
        ("memory", _step_memory),
        ("disk_size", _step_disk),
        ("capsules", _step_capsules),
        ("transient", _step_transient),
        ("destroy_on_stop", _step_destroy),
    ])

    recipe = dict(simple)
    if extra_state.get("cpus") is not None:
        recipe["cpus"] = extra_state["cpus"]
    if extra_state.get("memory") is not None:
        recipe["memory"] = extra_state["memory"]
    if extra_state.get("disk_size") is not None:
        recipe["disk_size"] = extra_state["disk_size"]
    if extra_state.get("capsules"):
        recipe["capsules"] = extra_state["capsules"]
    if extra_state.get("transient"):
        recipe.setdefault("ephemeral", {})["transient"] = True
    if extra_state.get("destroy_on_stop"):
        recipe.setdefault("ephemeral", {})["destroy_on_stop"] = True
    return recipe


def interactive_create_full() -> dict[str, Any]:
    """Full wizard — every knob exposed, grouped by category."""
    console.print("\n[bold]Create a new VM — Full Wizard[/bold]\n")

    def _step_profile(state: dict[str, Any], back: bool) -> str:
        return ask_select("Profile", ["headless", "desktop"], default="headless", allow_back=back)

    def _step_name(state: dict[str, Any], back: bool) -> str:
        profile = state.get("profile", "headless")
        name = ask_text("VM name", default=_suggest_name(profile), allow_back=back)
        validate_name(name)
        return name

    # Resources
    def _step_cpus(state: dict[str, Any], back: bool) -> int:
        return validate_cpus(ask_text("vCPUs", default="2", allow_back=back))

    def _step_memory(state: dict[str, Any], back: bool) -> int:
        return validate_memory(ask_text("Memory (MiB)", default="4096", allow_back=back))

    def _step_disk(state: dict[str, Any], back: bool) -> str:
        return validate_disk_size(ask_text("Disk size", default="20G", allow_back=back))

    def _step_guest_user(state: dict[str, Any], back: bool) -> str:
        return ask_text("Guest user", default="dev", allow_back=back)

    # Network
    def _step_net_mode(state: dict[str, Any], back: bool) -> str:
        return ask_select(
            "Network mode",
            ["nat", "isolated", "direct", "auto"],
            default="nat",
            allow_back=back,
        )

    def _step_nat_network(state: dict[str, Any], back: bool) -> str | None:
        if state.get("net_mode") == "nat":
            return ask_text("NAT network", default="default", allow_back=back)
        return None

    def _step_uplink(state: dict[str, Any], back: bool) -> str | None:
        if state.get("net_mode") == "direct":
            return ask_text("Uplink interface", default=detect_default_uplink() or "", allow_back=back)
        return None

    def _step_auto_fallback(state: dict[str, Any], back: bool) -> str | None:
        if state.get("net_mode") == "auto":
            if ask_confirm("Set NAT fallback network?", default=True, allow_back=back):
                return ask_text("NAT fallback network", default="default", allow_back=back)
        return None

    def _step_mgmt_ip(state: dict[str, Any], back: bool) -> str:
        ip = ask_text("Management IP", default="10.31.0.10", allow_back=back)
        validate_ip(ip)
        return ip

    # Lifecycle
    def _step_transient(state: dict[str, Any], back: bool) -> bool:
        profile = state.get("profile", "headless")
        return ask_confirm("Transient (libvirt transient)?", default=profile == "headless", allow_back=back)

    def _step_destroy(state: dict[str, Any], back: bool) -> bool:
        return ask_confirm("Destroy on stop (ephemeral)?", default=False, allow_back=back)

    def _step_max_runs(state: dict[str, Any], back: bool) -> str:
        return ask_text("Max runs (empty for no limit)", default="", allow_back=back)

    def _step_expires(state: dict[str, Any], back: bool) -> str:
        return ask_text("Expires after hours (empty for no limit)", default="", allow_back=back)

    # Security
    def _step_selinux(state: dict[str, Any], back: bool) -> bool:
        return ask_confirm("Enable SELinux hardening?", default=True, allow_back=back)

    def _step_no_agent(state: dict[str, Any], back: bool) -> bool:
        return ask_confirm("Disable qemu-guest-agent?", default=True, allow_back=back)

    def _step_restrict(state: dict[str, Any], back: bool) -> bool:
        return ask_confirm("Restrict outbound network?", default=False, allow_back=back)

    def _step_allow_hosts(state: dict[str, Any], back: bool) -> list[str]:
        if state.get("restrict"):
            hosts_input = ask_text("Allowed hosts (comma separated, empty for none)", default="", allow_back=back)
            return [h.strip() for h in hosts_input.split(",") if h.strip()]
        return []

    # Capsules
    def _step_capsules(state: dict[str, Any], back: bool) -> list[str]:
        available = list(list_capsules().keys())
        if not available:
            return []
        return ask_checkbox("Select capsules", available, allow_back=back)

    # Desktop password
    def _step_login_hash(state: dict[str, Any], back: bool) -> str:
        if state.get("profile") == "desktop":
            console.print("\n[bold]Desktop authentication[/bold]\n")
            return hash_password_interactive()
        return ""

    state = _run_wizard([
        ("profile", _step_profile),
        ("name", _step_name),
        ("cpus", _step_cpus),
        ("memory", _step_memory),
        ("disk_size", _step_disk),
        ("guest_user", _step_guest_user),
        ("net_mode", _step_net_mode),
        ("nat_network", _step_nat_network),
        ("uplink", _step_uplink),
        ("auto_fallback", _step_auto_fallback),
        ("mgmt_ip", _step_mgmt_ip),
        ("transient", _step_transient),
        ("destroy_on_stop", _step_destroy),
        ("max_runs", _step_max_runs),
        ("expires", _step_expires),
        ("selinux", _step_selinux),
        ("no_agent", _step_no_agent),
        ("restrict", _step_restrict),
        ("allow_hosts", _step_allow_hosts),
        ("capsules", _step_capsules),
        ("login_hash", _step_login_hash),
    ])

    recipe = {
        "profile": state["profile"],
        "template_name": state["profile"],
        "name": state["name"],
        "os_family": "fedora",
        "base_image": "",
        "cpus": state["cpus"],
        "memory": state["memory"],
        "disk_size": state["disk_size"],
        "guest_user": state["guest_user"],
        "passwordless_sudo": True,
        "network": {
            "mode": state["net_mode"],
            "nat_network": state.get("nat_network") or state.get("auto_fallback") or "",
            "uplink": state.get("uplink") or None,
            "mgmt_ip": state["mgmt_ip"],
            "mgmt_prefix": "24",
        },
        "ephemeral": {
            "transient": state["transient"],
            "destroy_on_stop": state["destroy_on_stop"],
            "max_runs": int(state["max_runs"]) if state.get("max_runs", "").strip() else None,
            "expires_after_hours": int(state["expires"]) if state.get("expires", "").strip() else None,
        },
        "security": {
            "selinux": state["selinux"],
            "no_guest_agent": state["no_agent"],
            "restrict_network": state["restrict"],
            "allow_hosts": state["allow_hosts"],
        },
        "capsules": state["capsules"],
        "provision": {
            "packages": [],
            "write_files": [],
            "root_commands": [],
            "user_commands": [],
        },
    }

    if state["profile"] == "desktop":
        recipe["login_hash"] = state["login_hash"]

    return recipe


# ---------------------------------------------------------------------------
# Template generator wizard
# ---------------------------------------------------------------------------

def interactive_generate_template() -> dict[str, Any]:
    """Wizard that outputs a raw template dict (no name field)."""
    console.print("\n[bold]Generate a .latita template[/bold]\n")

    def _step_profile(state: dict[str, Any], back: bool) -> str:
        return ask_select("Profile", ["headless", "desktop"], default="headless", allow_back=back)

    def _step_description(state: dict[str, Any], back: bool) -> str:
        return ask_text("Description", default="My custom template", allow_back=back)

    def _step_os_family(state: dict[str, Any], back: bool) -> str:
        return ask_select(
            "OS family", ["fedora", "ubuntu", "debian", "alpine"], default="fedora", allow_back=back
        )

    def _step_cpus(state: dict[str, Any], back: bool) -> int:
        return validate_cpus(ask_text("vCPUs", default="2", allow_back=back))

    def _step_memory(state: dict[str, Any], back: bool) -> int:
        return validate_memory(ask_text("Memory (MiB)", default="4096", allow_back=back))

    def _step_disk(state: dict[str, Any], back: bool) -> str:
        return validate_disk_size(ask_text("Disk size", default="20G", allow_back=back))

    def _step_guest_user(state: dict[str, Any], back: bool) -> str:
        return ask_text("Guest user", default="dev", allow_back=back)

    def _step_net_mode(state: dict[str, Any], back: bool) -> str:
        return ask_select(
            "Default network mode",
            ["nat", "isolated", "direct", "auto"],
            default="nat",
            allow_back=back,
        )

    def _step_nat_network(state: dict[str, Any], back: bool) -> str | None:
        if state.get("net_mode") == "nat":
            return ask_text("Default NAT network", default="default", allow_back=back)
        return None

    def _step_transient(state: dict[str, Any], back: bool) -> bool:
        profile = state.get("profile", "headless")
        return ask_confirm("Transient by default?", default=profile == "headless", allow_back=back)

    def _step_destroy(state: dict[str, Any], back: bool) -> bool:
        return ask_confirm("Destroy on stop by default?", default=False, allow_back=back)

    def _step_selinux(state: dict[str, Any], back: bool) -> bool:
        return ask_confirm("SELinux hardening?", default=True, allow_back=back)

    def _step_no_agent(state: dict[str, Any], back: bool) -> bool:
        return ask_confirm("Disable qemu-guest-agent?", default=True, allow_back=back)

    def _step_packages(state: dict[str, Any], back: bool) -> list[str]:
        raw = ask_text("Default packages (comma separated, empty for none)", default="", allow_back=back)
        if raw.strip():
            return [p.strip() for p in raw.split(",") if p.strip()]
        return []

    state = _run_wizard([
        ("profile", _step_profile),
        ("description", _step_description),
        ("os_family", _step_os_family),
        ("cpus", _step_cpus),
        ("memory", _step_memory),
        ("disk_size", _step_disk),
        ("guest_user", _step_guest_user),
        ("net_mode", _step_net_mode),
        ("nat_network", _step_nat_network),
        ("transient", _step_transient),
        ("destroy_on_stop", _step_destroy),
        ("selinux", _step_selinux),
        ("no_agent", _step_no_agent),
        ("packages", _step_packages),
    ])

    template: dict[str, Any] = {
        "profile": state["profile"],
        "os_family": state["os_family"],
        "description": state["description"],
        "cpus": state["cpus"],
        "memory": state["memory"],
        "disk_size": state["disk_size"],
        "guest_user": state["guest_user"],
        "passwordless_sudo": True,
        "network": {
            "mode": state["net_mode"],
            "nat_network": state.get("nat_network") or "",
            "mgmt_ip": "10.31.0.10",
            "mgmt_prefix": "24",
        },
        "ephemeral": {
            "transient": state["transient"],
            "destroy_on_stop": state["destroy_on_stop"],
        },
        "security": {
            "selinux": state["selinux"],
            "no_guest_agent": state["no_agent"],
            "restrict_network": False,
            "allow_hosts": [],
        },
        "provision": {
            "packages": state["packages"],
            "write_files": [],
            "root_commands": [],
            "user_commands": [],
        },
    }

    return template
