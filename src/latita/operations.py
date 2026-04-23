from __future__ import annotations

import datetime
import re
import shutil
import subprocess
import urllib.request
from copy import deepcopy
from pathlib import Path
from typing import Any

import click
import typer
from rich.table import Table

from . import capsules
from .cloudinit import build_network_config, build_user_data
from .config import BASE_IMAGES, get_config, load_latita_template
from .hardening import SecurityProfile, apply_hardening_to_args
from .libvirt import (
    autostart_network,
    define_network,
    ensure_network_active,
    ensure_network_exists,
    get_vm_ip_addresses,
    get_vm_interfaces,
    get_vm_state,
    get_vm_wan_ip,
    grant_qemu_path_access,
    iface_exists,
    iface_is_wireless,
    network_exists,
    network_is_active,
    random_mac,
    start_network,
    stop_vm_libvirt,
    start_vm_libvirt,
    resume_vm_libvirt,
    undefine_vm_libvirt,
    vm_exists,
    virt_install,
    write_mgmt_network_xml,
    create_network_xml,
)
from . import metadata
from .metadata import (
    read_instance_env,
    read_instance_recipe,
    read_instance_spec,
    write_instance_env,
    write_instance_recipe,
    write_instance_spec,
)
from .ui import console
from .utils import (
    create_lab_key,
    default_host_pubkey,
    hash_password_interactive,
    host_key_exists,
    lab_key_exists,
    need_cmd,
    read_text,
    run,
    shred_file,
    validate_ip,
    validate_name,
)


# ---------------------------------------------------------------------------
# OS info mapping
# ---------------------------------------------------------------------------

def _osinfo_for_recipe(recipe: dict[str, Any]) -> str:
    os_family = recipe.get("os_family", "fedora")
    base_image = recipe.get("base_image", "")
    if "ubuntu" in os_family or "ubuntu" in base_image.lower():
        return "detect=on,name=ubuntu24.04,require=off"
    if "debian" in os_family or "debian" in base_image.lower():
        return "detect=on,name=debian12,require=off"
    if "fedora" in os_family:
        version = "43"
        # try to extract version from base_image filename
        import re
        m = re.search(r'fedora(\d+)', base_image.lower())
        if m:
            version = m.group(1)
        return f"detect=on,name=fedora{version},require=off"
    return "detect=on,name=linux2024,require=off"


def _package_manager_for_recipe(recipe: dict[str, Any]) -> str:
    os_family = recipe.get("os_family", "fedora")
    if os_family in ("ubuntu", "debian"):
        return "apt"
    if os_family in ("alpine",):
        return "apk"
    return "dnf"


# ---------------------------------------------------------------------------
# Template normalization
# ---------------------------------------------------------------------------

def normalize_template(data: dict[str, Any]) -> dict[str, Any]:
    """Take a raw .latita template and fill defaults."""
    d = dict(data)
    profile = str(d.get("profile", "headless")).lower()
    if profile not in ("headless", "desktop"):
        raise typer.BadParameter(f"template profile must be headless or desktop, got {profile}")

    net = d.get("network") or {}
    ephemeral = d.get("ephemeral") or {}
    security = d.get("security") or {}
    provision = d.get("provision") or {}

    return {
        "profile": profile,
        "os_family": str(d.get("os_family", "fedora")).lower(),
        "description": str(d.get("description", "")),
        "base_image": str(d.get("base_image", get_config().default_base_name)),
        "cpus": int(d.get("cpus", 2)),
        "memory": int(d.get("memory", 4096)),
        "disk_size": str(d.get("disk_size", "20G")),
        "guest_user": str(d.get("guest_user", "dev")),
        "passwordless_sudo": bool(d.get("passwordless_sudo", True)),
        "network": {
            "mode": str(net.get("mode", "nat")).lower(),
            "nat_network": str(net.get("nat_network", "default")),
            "uplink": str(net.get("uplink", "")).strip() or None,
            "mgmt_ip": str(net.get("mgmt_ip", "10.31.0.10")),
            "mgmt_prefix": str(net.get("mgmt_prefix", "24")),
        },
        "ephemeral": {
            "transient": bool(ephemeral.get("transient", profile == "headless")),
            "destroy_on_stop": bool(ephemeral.get("destroy_on_stop", False)),
            "max_runs": int(ephemeral["max_runs"]) if ephemeral.get("max_runs") is not None else None,
            "expires_after_hours": int(ephemeral["expires_after_hours"]) if ephemeral.get("expires_after_hours") is not None else None,
        },
        "security": {
            "selinux": bool(security.get("selinux", True)),
            "no_guest_agent": bool(security.get("no_guest_agent", True)),
            "restrict_network": bool(security.get("restrict_network", False)),
            "allow_hosts": list(security.get("allow_hosts", [])),
            "readonly_root": bool(security.get("readonly_root", False)),
        },
        "capsules": list(d.get("capsules", [])),
        "provision": {
            "packages": list(provision.get("packages", [])),
            "write_files": list(provision.get("write_files", [])),
            "root_commands": list(provision.get("root_commands", [])),
            "user_commands": list(provision.get("user_commands", [])),
        },
    }


# ---------------------------------------------------------------------------
# Recipe helpers
# ---------------------------------------------------------------------------

def _default_keys() -> dict[str, str]:
    cfg = get_config()
    host = default_host_pubkey()
    lab = cfg.keys_dir / "lab1_ed25519.pub"
    return {
        "host_pubkey_path": str(host) if host else "",
        "lab_pubkey_path": str(lab) if lab.exists() else "",
        "lab_privkey_path": str(cfg.keys_dir / "lab1_ed25519"),
    }


def build_recipe(
    template_name: str,
    overrides: dict[str, Any] | None = None,
    capsule_names: list[str] | None = None,
) -> dict[str, Any]:
    template = normalize_template(load_latita_template(template_name))
    recipe = deepcopy(template)
    recipe["template_name"] = template_name
    if overrides:
        _deep_update(recipe, overrides)

    # Resolve capsules (validate compatibility)
    requested = list(capsule_names or recipe.get("capsules", []))
    if requested:
        resolved = capsules.resolve_capsules(
            requested,
            profile=recipe["profile"],
            os_family=recipe["os_family"],
        )
        recipe["_resolved_capsules"] = resolved
        recipe["capsules"] = requested
    else:
        recipe["_resolved_capsules"] = []

    # Keys
    keys = _default_keys()
    if not keys["host_pubkey_path"]:
        if host_key_exists():
            keys["host_pubkey_path"] = str(default_host_pubkey())
        else:
            keys["host_pubkey_path"] = str(create_lab_key("lab1").with_suffix(".pub"))
    if not keys["lab_pubkey_path"]:
        keys["lab_pubkey_path"] = str(create_lab_key("lab1").with_suffix(".pub"))
        keys["lab_privkey_path"] = str(get_config().keys_dir / "lab1_ed25519")

    recipe["_keys"] = keys
    return recipe


def _deep_update(base: dict[str, Any], override: dict[str, Any]) -> None:
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_update(base[k], v)
        else:
            base[k] = v


# ---------------------------------------------------------------------------
# Host bootstrap
# ---------------------------------------------------------------------------

def bootstrap_host() -> None:
    cfg = get_config()
    console.print("Bootstrapping latita...\n")
    need_cmd("virsh", "ssh-keygen", "qemu-img")
    if not cfg.is_session:
        need_cmd("setfacl")
    cfg.ensure_dirs()
    if not cfg.is_session:
        grant_qemu_path_access()

    console.print(f"  [green]✓[/green] Root: {cfg.root_dir}")
    console.print(f"  [green]✓[/green] Keys: {cfg.keys_dir}")
    console.print(f"  [green]✓[/green] VMs: {cfg.inst_dir}")

    # SSH keys
    if not host_key_exists():
        create_host_key()
    console.print(f"  [green]✓[/green] Host key: {default_host_pubkey()}")

    lab_key = cfg.keys_dir / "lab1_ed25519"
    if not lab_key.exists():
        create_lab_key("lab1")
    console.print(f"  [green]✓[/green] Lab key: {lab_key}")

    if not cfg.is_session:
        # Management network
        xml_path = write_mgmt_network_xml(cfg)
        if not network_exists(cfg.net_name):
            run(["virsh", "-c", cfg.libvirt_uri, "net-define", str(xml_path)], sudo=True)
            console.print(f"  [green]✓[/green] Defined network: {cfg.net_name}")
        if not network_is_active(cfg.net_name):
            run(["virsh", "-c", cfg.libvirt_uri, "net-start", cfg.net_name], sudo=True)
            console.print(f"  [green]✓[/green] Started network: {cfg.net_name}")
        run(["virsh", "-c", cfg.libvirt_uri, "net-autostart", cfg.net_name], sudo=True)
        console.print(f"  [green]✓[/green] Network autostart enabled")

        # Default NAT network
        if not network_exists("default"):
            xml = create_network_xml(
                name="default",
                mode="nat",
                ip_address="192.168.122.1",
                netmask="255.255.255.0",
                dhcp_start="192.168.122.100",
                dhcp_end="192.168.122.200",
            )
            p = cfg.net_dir / "default.xml"
            p.write_text(xml)
            define_network(p)
            start_network("default")
            autostart_network("default")
            console.print("  [green]✓[/green] Created NAT network: default")
        else:
            console.print("  [green]✓[/green] NAT network: default")
    else:
        console.print("  [dim]Session mode: skipped network setup[/dim]")

    # Base image
    base_img = cfg.base_dir / cfg.default_base_name
    if not base_img.exists():
        console.print(f"\n[yellow]Base image not found: {base_img}[/yellow]")
        if typer.confirm("Download Fedora 43 base image now?", default=True):
            init_base(cfg.default_base_name, cfg.default_base_url)
            console.print(f"  [green]✓[/green] Downloaded: {cfg.default_base_name}")
        else:
            console.print("  Skipped. Run 'latita init-base' later.", style="yellow")
    else:
        console.print(f"\n  [green]✓[/green] Base image: {base_img}")

    console.print("\n[green]Bootstrap complete![/green]")


# ---------------------------------------------------------------------------
# Base image download
# ---------------------------------------------------------------------------

def init_base(name: str | None = None, url: str | None = None) -> None:
    need_cmd("curl", "qemu-img")
    get_config().ensure_dirs()
    if name and url:
        _download_base(name, url)
        return
    choices = list(BASE_IMAGES.keys()) + ["cancel"]
    choice = typer.prompt(
        "Choose base image",
        type=click.Choice(choices),
        default=choices[0],
    )
    if choice == "cancel":
        return
    info = BASE_IMAGES[choice]
    _download_base(info["filename"], info["url"])


def _discover_latest_fedora_url(url: str) -> str | None:
    """If a Fedora Cloud image URL 404s, scrape the directory listing for the latest Generic qcow2."""
    # Derive the directory URL from the file URL
    dir_url = url.rsplit("/", 1)[0] + "/"
    try:
        with urllib.request.urlopen(dir_url, timeout=20) as resp:  # noqa: S310
            html = resp.read().decode("utf-8", errors="ignore")
    except Exception:
        return None
    # Extract filenames like Fedora-Cloud-Base-Generic-43-1.6.x86_64.qcow2
    matches = re.findall(
        r'href="(Fedora-Cloud-Base-Generic-\d+(?:\.\d+)*-[\d.]+\.x86_64\.qcow2)"',
        html,
    )
    if not matches:
        return None
    # Sort by version string and pick the latest
    matches.sort(key=lambda s: [int(x) for x in re.findall(r"\d+", s)])
    return dir_url + matches[-1]


def _download_base(name: str, url: str) -> None:
    cfg = get_config()
    dst = cfg.base_dir / name
    if dst.exists():
        console.print(f"Base image already exists: {dst}", style="green")
        return
    try:
        run(["curl", "-L", "--fail", "-o", str(dst), url])
    except subprocess.CalledProcessError as exc:
        if exc.returncode == 22:  # curl --fail 404
            discovered = _discover_latest_fedora_url(url)
            if discovered and discovered != url:
                console.print(f"[yellow]Base image URL 404; retrying with discovered URL:[/yellow] {discovered}")
                run(["curl", "-L", "--fail", "-o", str(dst), discovered])
            else:
                raise
        else:
            raise
    dst.chmod(0o444)
    run(["qemu-img", "info", str(dst)])
    console.print(f"Downloaded: {dst}", style="green")


# ---------------------------------------------------------------------------
# Instance creation
# ---------------------------------------------------------------------------

def create_instance(
    template_name: str,
    name: str | None = None,
    capsule_names: list[str] | None = None,
    overrides: dict[str, Any] | None = None,
) -> None:
    cfg = get_config()
    validate_name(name or "")
    recipe = build_recipe(template_name, overrides=overrides, capsule_names=capsule_names)
    if name:
        recipe["name"] = name
    else:
        recipe["name"] = _suggest_name(recipe["profile"])

    validate_name(recipe["name"])
    validate_ip(recipe["network"]["mgmt_ip"])

    inst = cfg.inst_dir / recipe["name"]
    if inst.exists():
        raise typer.BadParameter(f"instance already exists: {inst}")

    base_img = cfg.base_dir / recipe["base_image"]
    if not base_img.exists():
        raise typer.BadParameter(f"base image not found: {base_img}")

    need_cmd("qemu-img", "virt-install", "virsh")
    cfg.ensure_dirs()
    if not cfg.is_session:
        grant_qemu_path_access()
        ensure_network_exists(cfg.net_name)

    # Networking
    net = recipe["network"]
    net_mode = net["mode"]
    nat_network = net["nat_network"]
    uplink = net.get("uplink")

    if cfg.is_session:
        net_mode = "user"
    elif net_mode == "auto":
        from .libvirt import detect_default_uplink
        uplink = uplink or detect_default_uplink()
        if not uplink:
            raise typer.BadParameter("could not detect default uplink")
        if iface_is_wireless(uplink):
            net_mode = "nat"
            if not nat_network:
                raise typer.BadParameter("wifi detected; pass nat_network")
        else:
            net_mode = "direct"

    if net_mode in ("isolated", "none"):
        pass  # no host-side setup needed
    elif net_mode == "direct":
        if not uplink or not iface_exists(uplink):
            raise typer.BadParameter(f"uplink does not exist: {uplink}")
        if iface_is_wireless(uplink):
            raise typer.BadParameter(f"uplink {uplink} is wireless; use nat mode")
    elif net_mode == "nat":
        if not nat_network:
            raise typer.BadParameter("nat_network required for nat mode")
        ensure_network_active(nat_network)
    elif net_mode == "user":
        pass  # no host-side setup needed
    else:
        raise typer.BadParameter("network mode must be isolated, nat, direct, auto, or user")

    inst.mkdir(parents=True)
    overlay = inst / f"{recipe['name']}.qcow2"

    # Pre-flight checks
    for key_path in (recipe["_keys"]["host_pubkey_path"], recipe["_keys"]["lab_pubkey_path"]):
        if key_path and not Path(key_path).exists():
            console.print(f"[yellow]Warning: key not found: {key_path}[/yellow]")

    try:
        _run_create(recipe, inst, overlay, net_mode, nat_network, uplink)
    except Exception as exc:
        console.print(f"\n[red]Creation failed: {exc}[/red]")
        console.print("[yellow]Rolling back instance directory...[/yellow]")
        _rollback_create(inst)
        raise


def _rollback_create(inst: Path) -> None:
    if inst.exists():
        for f in inst.iterdir():
            if f.is_file():
                shred_file(f)
        shutil.rmtree(inst)


def _run_create(
    recipe: dict[str, Any],
    inst: Path,
    overlay: Path,
    net_mode: str,
    nat_network: str,
    uplink: str | None,
) -> None:
    cfg = get_config()
    base_img = cfg.base_dir / recipe["base_image"]
    net = recipe["network"]

    # Build cloud-init
    keys = recipe["_keys"]
    host_pubkey = read_text(Path(keys["host_pubkey_path"]))
    lab_pubkey = read_text(Path(keys["lab_pubkey_path"])) if keys["lab_pubkey_path"] else ""

    capsule_provisions = [
        capsules.capsule_provision_fragment(c)
        for c in recipe.get("_resolved_capsules", [])
    ]

    # Desktop needs login hash if not passwordless_sudo
    login_hash = ""
    if recipe["profile"] == "desktop":
        login_hash = hash_password_interactive()

    pkg_mgr = _package_manager_for_recipe(recipe)
    osinfo = _osinfo_for_recipe(recipe)

    user_data = build_user_data(
        profile=recipe["profile"],
        guest_user=recipe["guest_user"],
        host_pubkey=host_pubkey,
        lab_pubkey=lab_pubkey,
        lab_privkey=Path(keys["lab_privkey_path"]) if keys.get("lab_privkey_path") else None,
        login_hash=login_hash,
        provision=recipe["provision"],
        capsule_provisions=capsule_provisions,
        passwordless_sudo=recipe["passwordless_sudo"],
        package_manager=pkg_mgr,
    )

    wan_mac = random_mac()
    mgmt_mac = random_mac()
    net_cfg = build_network_config(
        wan_mac, mgmt_mac, net["mgmt_ip"], net["mgmt_prefix"]
    )

    ud_path = inst / "user-data.yaml"
    nc_path = inst / "network-config.yaml"
    ud_path.write_text(user_data)
    nc_path.write_text(net_cfg)

    run(["qemu-img", "create", "-f", "qcow2", "-F", "qcow2", "-b", str(base_img), str(overlay)])
    run(["qemu-img", "resize", str(overlay), recipe["disk_size"]])

    # Virt-install args
    args = [
        "--name", recipe["name"],
        "--memory", str(recipe["memory"]),
        "--vcpus", str(recipe["cpus"]),
        "--cpu", "host-passthrough",
        "--import",
        "--osinfo", osinfo,
        "--disk", f"path={overlay},format=qcow2,bus=virtio,discard=unmap",
        "--cloud-init", f"user-data={ud_path},network-config={nc_path},disable=on",
        "--rng", "/dev/urandom",
        "--noautoconsole",
    ]

    if recipe["ephemeral"]["transient"]:
        args.append("--transient")

    if recipe["profile"] == "desktop":
        args.extend([
            "--graphics", "spice,listen=127.0.0.1",
            "--video", "qxl",
            "--channel", "spicevmc",
        ])
    else:
        args.extend(["--graphics", "none"])

    # Networking
    if net_mode == "isolated" or net_mode == "none":
        args.extend(["--network", "none"])
    elif net_mode == "direct":
        args.extend(["--network", f"type=direct,source={uplink},source_mode=private,model=virtio,mac={wan_mac}"])
    elif net_mode == "user":
        args.extend(["--network", f"type=user,model=virtio,mac={wan_mac}"])
    else:
        args.extend(["--network", f"network={nat_network},model=virtio,mac={wan_mac}"])
    if not cfg.is_session and net_mode not in ("isolated", "none"):
        args.extend(["--network", f"network={cfg.net_name},model=virtio,mac={mgmt_mac}"])

    # Security hardening
    sec = recipe["security"]
    profile = SecurityProfile.from_dict(sec)
    args = apply_hardening_to_args(profile, args, vm_name=recipe["name"])

    virt_install(args)

    # Spec & metadata
    now = datetime.datetime.now(datetime.timezone.utc)
    expire_at = None
    hours = recipe["ephemeral"].get("expires_after_hours")
    if hours:
        expire_at = (now + datetime.timedelta(hours=hours)).isoformat()

    spec = {
        "role": recipe["profile"],
        "template_name": recipe["template_name"],
        "overlay": str(overlay),
        "wan_mac": wan_mac,
        "mgmt_mac": mgmt_mac,
        "net_mode": net_mode,
        "nat_network": nat_network,
        "uplink": uplink,
        "graphics": "spice" if recipe["profile"] == "desktop" else "none",
        "transient": recipe["ephemeral"]["transient"],
        "destroy_on_stop": recipe["ephemeral"]["destroy_on_stop"],
        "max_runs": recipe["ephemeral"]["max_runs"],
        "expire_at": expire_at,
        "run_count": 0,
        "created_at": now.isoformat(),
        "base_image": recipe["base_image"],
        "osinfo": osinfo,
    }
    write_instance_recipe(recipe["name"], recipe)
    write_instance_spec(recipe["name"], spec)
    write_instance_env(recipe["name"], {
        "NAME": recipe["name"],
        "TEMPLATE": recipe["template_name"],
        "PROFILE": recipe["profile"],
        "MGMT_IP": net["mgmt_ip"],
        "GUEST_USER": recipe["guest_user"],
        "TRANSIENT": "yes" if spec["transient"] else "no",
        "DESTROY_ON_STOP": "yes" if spec["destroy_on_stop"] else "no",
        "MAX_RUNS": str(spec["max_runs"] or ""),
        "EXPIRE_AT": str(spec["expire_at"] or ""),
        "GRAPHICS": spec["graphics"],
    })

    console.print(f"\n[green]Created {recipe['name']} from template '{recipe['template_name']}'[/green]")
    console.print(f"  Profile : {recipe['profile']}")
    console.print(f"  IP      : {net['mgmt_ip']}")
    console.print(f"  Overlay : {overlay}")
    if recipe["ephemeral"]["transient"]:
        console.print(f"  Mode    : transient (libvirt)")
    if recipe["ephemeral"]["destroy_on_stop"]:
        console.print(f"  Mode    : ephemeral (destroy on stop)")
    if hours:
        console.print(f"  Expires : {expire_at}")
    if recipe["ephemeral"]["max_runs"]:
        console.print(f"  Max runs: {recipe['ephemeral']['max_runs']}")
    console.print(f"\nSSH: latita ssh {recipe['name']}")


# ---------------------------------------------------------------------------
# Instance lifecycle
# ---------------------------------------------------------------------------

def _check_ephemeral_constraints(name: str) -> None:
    spec = read_instance_spec(name)
    if not spec:
        return

    # Expiration
    expire_at = spec.get("expire_at")
    if expire_at:
        dt = datetime.datetime.fromisoformat(expire_at)
        if datetime.datetime.now(datetime.timezone.utc) > dt:
            raise typer.BadParameter(
                f"VM '{name}' has expired ({expire_at}). Destroy it with 'latita destroy {name}'"
            )

    # Max runs
    max_runs = spec.get("max_runs")
    if max_runs is not None:
        count = metadata.get_run_count(name)
        if count >= max_runs:
            raise typer.BadParameter(
                f"VM '{name}' reached max runs ({max_runs}). Destroy it with 'latita destroy {name}'"
            )


def start_instance(name: str) -> None:
    validate_name(name)
    if not vm_exists(name):
        raise typer.BadParameter(f"VM not found in libvirt: {name}")
    _check_ephemeral_constraints(name)
    state = get_vm_state(name)
    if state == "running":
        console.print(f"{name} is already running", style="yellow")
        return
    if state == "paused":
        resume_vm_libvirt(name)
    else:
        start_vm_libvirt(name)
    metadata.increment_run_count(name)
    console.print(f"Started {name}", style="green")


def stop_instance(name: str) -> None:
    validate_name(name)
    spec = read_instance_spec(name)
    if spec and spec.get("destroy_on_stop"):
        console.print(f"VM '{name}' is ephemeral (destroy_on_stop). Destroying...", style="yellow")
        destroy_instance(name)
        return

    if not vm_exists(name):
        raise typer.BadParameter(f"VM not found in libvirt: {name}")
    state = get_vm_state(name)
    if state == "shut off":
        console.print(f"{name} is already stopped", style="yellow")
        return
    stop_vm_libvirt(name)
    console.print(f"Stopped {name}", style="green")


def destroy_instance(name: str) -> None:
    validate_name(name)
    cfg = get_config()
    if vm_exists(name):
        stop_vm_libvirt(name)
        undefine_vm_libvirt(name)
    inst = cfg.inst_dir / name
    if inst.exists():
        overlay = inst / f"{name}.qcow2"
        shred_file(overlay)
        for f in inst.iterdir():
            if f.is_file():
                shred_file(f)
        shutil.rmtree(inst)
    console.print(f"Destroyed {name}", style="green")


# ---------------------------------------------------------------------------
# One-shot ephemeral runner (like smolvm machine run)
# ---------------------------------------------------------------------------

def run_instance(
    template_name: str,
    command: list[str] | None = None,
    overrides: dict[str, Any] | None = None,
    capsule_names: list[str] | None = None,
) -> None:
    """Create a transient, one-shot VM with no persistent state."""
    cfg = get_config()
    recipe = build_recipe(template_name, overrides=overrides, capsule_names=capsule_names)

    # Force ephemeral settings
    recipe["ephemeral"]["transient"] = True
    recipe["ephemeral"]["destroy_on_stop"] = False
    recipe["network"]["mode"] = "user" if cfg.is_session else recipe["network"].get("mode", "isolated")

    name = recipe.get("name") or _suggest_name(recipe["profile"])
    validate_name(name)
    recipe["name"] = name

    base_img = cfg.base_dir / recipe["base_image"]
    if not base_img.exists():
        raise typer.BadParameter(f"base image not found: {base_img}")

    need_cmd("qemu-img", "virt-install", "virsh")
    cfg.ensure_dirs()
    if not cfg.is_session:
        grant_qemu_path_access()

    # Use a temp overlay that will be deleted after
    import tempfile
    with tempfile.TemporaryDirectory(prefix="latita-run-") as td:
        overlay = Path(td) / f"{name}.qcow2"
        run(["qemu-img", "create", "-f", "qcow2", "-F", "qcow2", "-b", str(base_img), str(overlay)])
        run(["qemu-img", "resize", str(overlay), recipe["disk_size"]])

        keys = recipe["_keys"]
        host_pubkey = read_text(Path(keys["host_pubkey_path"]))
        lab_pubkey = read_text(Path(keys["lab_pubkey_path"])) if keys["lab_pubkey_path"] else ""

        capsule_provisions = [
            capsules.capsule_provision_fragment(c)
            for c in recipe.get("_resolved_capsules", [])
        ]

        pkg_mgr = _package_manager_for_recipe(recipe)
        osinfo = _osinfo_for_recipe(recipe)

        user_data = build_user_data(
            profile=recipe["profile"],
            guest_user=recipe["guest_user"],
            host_pubkey=host_pubkey,
            lab_pubkey=lab_pubkey,
            lab_privkey=Path(keys["lab_privkey_path"]) if keys.get("lab_privkey_path") else None,
            login_hash="",
            provision=recipe["provision"],
            capsule_provisions=capsule_provisions,
            passwordless_sudo=recipe["passwordless_sudo"],
            package_manager=pkg_mgr,
        )

        wan_mac = random_mac()
        net = recipe["network"]
        net_cfg = build_network_config(wan_mac, random_mac(), net["mgmt_ip"], net["mgmt_prefix"])

        ud_path = Path(td) / "user-data.yaml"
        nc_path = Path(td) / "network-config.yaml"
        ud_path.write_text(user_data)
        nc_path.write_text(net_cfg)

        args = [
            "--name", name,
            "--memory", str(recipe["memory"]),
            "--vcpus", str(recipe["cpus"]),
            "--cpu", "host-passthrough",
            "--import",
            "--osinfo", osinfo,
            "--disk", f"path={overlay},format=qcow2,bus=virtio,discard=unmap",
            "--cloud-init", f"user-data={ud_path},network-config={nc_path},disable=on",
            "--rng", "/dev/urandom",
            "--noautoconsole",
            "--transient",
        ]

        if recipe["profile"] == "desktop":
            args.extend([
                "--graphics", "spice,listen=127.0.0.1",
                "--video", "qxl",
                "--channel", "spicevmc",
            ])
        else:
            args.extend(["--graphics", "none"])

        net_mode = net["mode"]
        if net_mode in ("isolated", "none"):
            args.extend(["--network", "none"])
        elif net_mode == "user":
            args.extend(["--network", f"type=user,model=virtio,mac={wan_mac}"])
        elif net_mode == "direct" and net.get("uplink"):
            args.extend(["--network", f"type=direct,source={net['uplink']},source_mode=private,model=virtio,mac={wan_mac}"])
        else:
            nat_network = net.get("nat_network", "default")
            args.extend(["--network", f"network={nat_network},model=virtio,mac={wan_mac}"])
        if not cfg.is_session and net_mode not in ("isolated", "none"):
            args.extend(["--network", f"network={cfg.net_name},model=virtio,mac={random_mac()}"])

        sec = recipe["security"]
        profile = SecurityProfile.from_dict(sec)
        args = apply_hardening_to_args(profile, args, vm_name=name)

        console.print(f"Running transient {name}...", style="cyan")
        virt_install(args)

        # If a command was given, wait briefly for boot then exec via SSH?
        # For now just let the user connect manually, or we can wait for shutdown
        if command:
            console.print(f"Transient VM started. Waiting for shutdown...", style="dim")
            # Wait until domain disappears (transient auto-removes on shutdown)
            import time
            while vm_exists(name):
                time.sleep(1)
        else:
            console.print(f"Transient VM {name} is running. Connect with: latita ssh {name}", style="green")
            console.print(f"It will disappear on shutdown.", style="dim")


# ---------------------------------------------------------------------------
# Revive
# ---------------------------------------------------------------------------

def revive_instance(name: str) -> None:
    validate_name(name)
    cfg = get_config()
    if vm_exists(name):
        state = get_vm_state(name)
        if state == "running":
            console.print(f"{name} is already running", style="yellow")
            return
        start_instance(name)
        return

    recipe = read_instance_recipe(name)
    spec = read_instance_spec(name)
    if not recipe or not spec:
        raise typer.BadParameter(f"no saved recipe/spec for {name}; cannot revive")

    overlay = Path(spec["overlay"])
    if not overlay.exists():
        raise typer.BadParameter(f"overlay missing: {overlay}")

    if not cfg.is_session:
        ensure_network_exists(cfg.net_name)
        if spec.get("nat_network"):
            ensure_network_active(spec["nat_network"])

    net_mode = spec.get("net_mode", "nat")
    nat_network = spec.get("nat_network", "default")
    uplink = spec.get("uplink")

    if cfg.is_session:
        net_mode = "user"

    wan_mac = spec.get("wan_mac", random_mac())
    mgmt_mac = spec.get("mgmt_mac", random_mac())
    osinfo = spec.get("osinfo", "detect=on,name=fedora43,require=off")

    args = [
        "--name", name,
        "--memory", str(recipe.get("memory", 4096)),
        "--vcpus", str(recipe.get("cpus", 2)),
        "--cpu", "host-passthrough",
        "--import",
        "--osinfo", osinfo,
        "--disk", f"path={overlay},format=qcow2,bus=virtio,discard=unmap",
        "--rng", "/dev/urandom",
        "--noautoconsole",
    ]
    if spec.get("transient"):
        args.append("--transient")

    if spec.get("graphics") == "spice":
        args.extend(["--graphics", "spice,listen=127.0.0.1", "--video", "qxl", "--channel", "spicevmc"])
    else:
        args.extend(["--graphics", "none"])

    if net_mode in ("isolated", "none"):
        args.extend(["--network", "none"])
    elif net_mode == "direct" and uplink and iface_exists(uplink):
        args.extend(["--network", f"type=direct,source={uplink},source_mode=private,model=virtio,mac={wan_mac}"])
    elif net_mode == "user":
        args.extend(["--network", f"type=user,model=virtio,mac={wan_mac}"])
    else:
        args.extend(["--network", f"network={nat_network},model=virtio,mac={wan_mac}"])
    if not cfg.is_session and net_mode not in ("isolated", "none"):
        args.extend(["--network", f"network={cfg.net_name},model=virtio,mac={mgmt_mac}"])

    sec_dict = recipe.get("security", {})
    profile = SecurityProfile.from_dict(sec_dict)
    args = apply_hardening_to_args(profile, args, vm_name=name)

    ud_path = cfg.inst_dir / name / "user-data.yaml"
    nc_path = cfg.inst_dir / name / "network-config.yaml"
    if ud_path.exists() and nc_path.exists():
        args.extend(["--cloud-init", f"user-data={ud_path},network-config={nc_path},disable=on"])

    virt_install(args)
    metadata.increment_run_count(name)
    console.print(f"Revived {name}", style="green")


# ---------------------------------------------------------------------------
# SSH / Connect / Live capsule apply
# ---------------------------------------------------------------------------

def get_vm_ip(name: str) -> str | None:
    # Prefer dynamic discovery (guest agent, DHCP lease, ARP) over static config
    addresses = get_vm_ip_addresses(name)
    if addresses:
        return addresses[0]["ip"]
    env = read_instance_env(name)
    mgmt_ip = env.get("MGMT_IP")
    if mgmt_ip:
        return mgmt_ip
    recipe = read_instance_recipe(name)
    if recipe:
        net = recipe.get("network", {})
        return net.get("mgmt_ip")
    return None


def ssh_instance(name: str, command: str | None = None) -> None:
    validate_name(name)
    ip = get_vm_ip(name)
    if not ip:
        raise typer.BadParameter(f"cannot resolve IP for {name}")
    env = read_instance_env(name)
    user = env.get("GUEST_USER", "dev")

    recipe = read_instance_recipe(name)
    key = None
    if recipe:
        keys = recipe.get("_keys", {})
        lab_priv = keys.get("lab_privkey_path")
        host_priv = keys.get("host_pubkey_path", "").replace(".pub", "")
        for candidate in (lab_priv, host_priv):
            if candidate and Path(candidate).exists():
                key = candidate
                break
    if not key:
        for kname in ("id_ed25519", "id_ecdsa", "id_rsa"):
            kp = Path.home() / ".ssh" / kname
            if kp.exists():
                key = str(kp)
                break
    if not key:
        raise typer.BadParameter("no SSH private key found")

    cmd = ["ssh", "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null", "-i", key, f"{user}@{ip}"]
    if command:
        cmd.append(command)
    subprocess.run(cmd)


def connect_instance(name: str) -> None:
    validate_name(name)
    spec = read_instance_spec(name)
    if spec and spec.get("graphics") == "spice":
        subprocess.run(["virt-viewer", "--connect", get_config().libvirt_uri, "--wait", name])
    else:
        ssh_instance(name)


def apply_capsule_live(name: str, capsule_name: str) -> None:
    validate_name(name)
    recipe = read_instance_recipe(name)
    if not recipe:
        raise typer.BadParameter(f"no saved recipe for {name}")

    capsule = capsules.load_capsule(capsule_name)
    ok, reason = capsules.check_capsule_compatibility(
        capsule,
        profile=recipe.get("profile", ""),
        os_family=recipe.get("os_family", ""),
    )
    if not ok:
        console.print(f"[yellow]Capsule '{capsule_name}' incompatible: {reason}[/yellow]")
        console.print("[yellow]You can still apply it manually via 'latita ssh {name}'[/yellow]")
        return

    cmds = capsules.capsule_live_commands(capsule)
    if not cmds:
        console.print(f"Capsule '{capsule_name}' has no live commands", style="yellow")
        return

    ip = get_vm_ip(name)
    if not ip:
        console.print(f"[yellow]Cannot resolve IP for {name}. Is the VM running?[/yellow]")
        console.print("[yellow]Try: latita start {name}[/yellow]")
        return

    user = capsules.capsule_live_user(capsule, recipe.get("guest_user", "dev"))

    keys = recipe.get("_keys", {})
    key = None
    for candidate in (keys.get("lab_privkey_path"), keys.get("host_pubkey_path", "").replace(".pub", "")):
        if candidate and Path(candidate).exists():
            key = candidate
            break
    if not key:
        for kname in ("id_ed25519", "id_ecdsa", "id_rsa"):
            kp = Path.home() / ".ssh" / kname
            if kp.exists():
                key = str(kp)
                break
    if not key:
        console.print("[yellow]No SSH private key found. Cannot apply capsule remotely.[/yellow]")
        return

    script = "\n".join(cmds)
    ssh_cmd = [
        "ssh",
        "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=/dev/null",
        "-o", "ConnectTimeout=5",
        "-i", key,
        f"{user}@{ip}",
        "bash", "-lc", script,
    ]
    console.print(f"Applying capsule '{capsule_name}' to {name} via SSH...", style="cyan")
    result = subprocess.run(ssh_cmd, capture_output=True, text=True)
    if result.returncode != 0:
        console.print(f"[yellow]Capsule apply returned non-zero ({result.returncode})[/yellow]")
        if result.stderr:
            console.print(f"[dim]{result.stderr[:500]}[/dim]")
        console.print("[yellow]Try debugging manually: latita ssh {name}[/yellow]")
    else:
        console.print(f"[green]Capsule '{capsule_name}' applied successfully[/green]")


# ---------------------------------------------------------------------------
# Inventory / listing
# ---------------------------------------------------------------------------

def scan_instances() -> list[dict[str, Any]]:
    cfg = get_config()
    cfg.ensure_dirs()
    names = {d.name for d in cfg.inst_dir.iterdir() if d.is_dir()} if cfg.inst_dir.exists() else set()
    try:
        cp = run(["virsh", "-c", cfg.libvirt_uri, "list", "--all", "--name"], capture=True, check=False)
        if cp.returncode == 0:
            names |= {line.strip() for line in cp.stdout.splitlines() if line.strip()}
    except Exception:
        pass

    entries: list[dict[str, Any]] = []
    for name in sorted(names):
        recipe = read_instance_recipe(name)
        spec = read_instance_spec(name)
        env = read_instance_env(name)
        overlay = cfg.inst_dir / name / f"{name}.qcow2"

        state = ""
        defined = False
        try:
            st = get_vm_state(name)
            state = st
            defined = bool(st)
        except Exception:
            pass

        interfaces = {}
        if state == "running":
            try:
                interfaces = get_vm_interfaces(name)
            except Exception:
                pass

        mgmt_ip = env.get("MGMT_IP", "")
        transient = env.get("TRANSIENT", "no") == "yes"
        destroy_on_stop = env.get("DESTROY_ON_STOP", "no") == "yes"
        expire_at = spec.get("expire_at")
        max_runs = spec.get("max_runs")
        run_count = spec.get("run_count", 0)

        status = state or ("stored" if overlay.exists() else "broken")
        entries.append({
            "name": name,
            "profile": recipe.get("profile", env.get("PROFILE", "unknown")) if recipe else env.get("PROFILE", "unknown"),
            "template": recipe.get("template_name", env.get("TEMPLATE", "")) if recipe else env.get("TEMPLATE", ""),
            "status": status,
            "mgmt_ip": mgmt_ip,
            "interfaces": interfaces,
            "transient": transient,
            "destroy_on_stop": destroy_on_stop,
            "expire_at": expire_at,
            "max_runs": max_runs,
            "run_count": run_count,
            "overlay_exists": overlay.exists(),
        })
    return entries


def list_instances() -> None:
    entries = scan_instances()
    if not entries:
        console.print("No instances found", style="yellow")
        return
    table = Table(title="Instances")
    table.add_column("Name")
    table.add_column("Template")
    table.add_column("Type")
    table.add_column("Status")
    table.add_column("Mgmt IP")
    table.add_column("Constraints")
    for e in entries:
        ctype = []
        if e["transient"]:
            ctype.append("transient")
        if e["destroy_on_stop"]:
            ctype.append("ephemeral")
        constraints = []
        if e["expire_at"]:
            constraints.append(f"expires {e['expire_at'][:10]}")
        if e["max_runs"] is not None:
            constraints.append(f"runs {e['run_count']}/{e['max_runs']}")
        table.add_row(
            e["name"],
            e["template"] or e["profile"],
            ", ".join(ctype) if ctype else "persistent",
            e["status"],
            e["mgmt_ip"] or "-",
            ", ".join(constraints) if constraints else "-",
        )
    console.print(table)


# ---------------------------------------------------------------------------
# Doctor
# ---------------------------------------------------------------------------

def doctor() -> None:
    cfg = get_config()
    table = Table(title="Doctor")
    table.add_column("Item")
    table.add_column("Status")
    for cmd in ["virsh", "virt-install", "qemu-img", "ssh", "curl", "ssh-keygen", "virt-viewer", "openssl", "setfacl"]:
        table.add_row(cmd, "ok" if shutil.which(cmd) else "missing")
    table.add_row("root", str(cfg.root_dir))
    table.add_row("uri", cfg.libvirt_uri)
    console.print(table)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _suggest_name(profile: str) -> str:
    prefix = "desktop-" if profile == "desktop" else "vm-"
    cfg = get_config()
    used = set()
    if cfg.inst_dir.exists():
        used |= {d.name for d in cfg.inst_dir.iterdir() if d.is_dir()}
    try:
        cp = run(["virsh", "-c", cfg.libvirt_uri, "list", "--all", "--name"], capture=True, check=False)
        if cp.returncode == 0:
            used |= {line.strip() for line in cp.stdout.splitlines() if line.strip()}
    except Exception:
        pass
    for idx in range(1, 10000):
        candidate = f"{prefix}{idx:03d}"
        if candidate not in used:
            return candidate
    return f"{prefix}unknown"
