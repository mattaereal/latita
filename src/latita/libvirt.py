from __future__ import annotations

from pathlib import Path
from random import randint
from typing import Any, Optional

import typer

from .config import get_config
from .utils import need_cmd, run, log_cmd


def virsh(*args: str, sudo: bool = False, capture: bool = False, check: bool = True):
    cfg = get_config()
    return run(
        ['virsh', '-c', cfg.libvirt_uri, *args], sudo=sudo, capture=capture, check=check
    )


def _system_python_site_packages() -> str:
    import subprocess, sysconfig
    for python in ['/usr/bin/python3', '/usr/local/bin/python3']:
        cp = subprocess.run(
            [python, '-c', 'import sys; sp=sys.path[-1]; print(sp if sp.endswith(\"site-packages\") else \"\")'],
            capture_output=True, text=True, timeout=10,
        )
        if cp.returncode == 0:
            path = cp.stdout.strip()
            if path and Path(path).exists():
                return path
    return ''


def virt_install(args: list[str]) -> None:
    cfg = get_config()
    cmd = ['virt-install', '--connect', cfg.libvirt_uri, *args]
    log_cmd(cmd)
    import os, subprocess
    env = dict(os.environ)
    sys_sp = _system_python_site_packages()
    if sys_sp:
        existing = env.get('PYTHONPATH', '')
        env['PYTHONPATH'] = f'{sys_sp}{os.pathsep}{existing}' if existing else sys_sp
    subprocess.run(cmd, env=env, check=True)


def ensure_network_exists(name: str) -> None:
    cp = virsh("net-info", name, capture=True, check=False)
    if cp.returncode != 0:
        raise typer.BadParameter(f"libvirt network '{name}' not defined")


def ensure_network_active(name: str) -> None:
    ensure_network_exists(name)
    cp = virsh("net-list", "--name", capture=True)
    if name not in cp.stdout.splitlines():
        raise typer.BadParameter(f"libvirt network '{name}' is not active")


def detect_default_uplink() -> Optional[str]:
    cp = run(["ip", "route", "get", "1.1.1.1"], capture=True, check=False)
    if cp.returncode != 0:
        return None
    parts = cp.stdout.split()
    for i, token in enumerate(parts[:-1]):
        if token == "dev":
            return parts[i + 1]
    return None


def iface_exists(name: str) -> bool:
    return Path(f"/sys/class/net/{name}").exists()


def iface_is_wireless(name: str) -> bool:
    return Path(f"/sys/class/net/{name}/wireless").exists()


def grant_qemu_path_access() -> None:
    need_cmd("setfacl")
    cfg = get_config()
    candidates = [Path.home(), cfg.root_dir.parent, cfg.root_dir, cfg.vm_dir, cfg.base_dir, cfg.inst_dir]
    for user in ("qemu", "libvirt-qemu"):
        cp = run(["id", user], check=False, capture=True)
        if cp.returncode != 0:
            continue
        for p in candidates:
            if p.exists():
                run(["setfacl", "-m", f"u:{user}:rx", str(p)], sudo=True, check=False)


def mgmt_network_xml(cfg: Config | None = None) -> str:
    cfg = cfg or get_config()
    return (
        f"<network ipv6='yes'>\n"
        f"  <name>{cfg.net_name}</name>\n"
        f"  <bridge name='virbr-mgmt' stp='on' delay='0'/>\n"
        f"  <mac address='52:54:00:aa:bb:cc'/>\n"
        f"</network>\n"
    )


def write_mgmt_network_xml(cfg: Config | None = None) -> Path:
    cfg = cfg or get_config()
    xml_path = cfg.net_dir / f"{cfg.net_name}.xml"
    xml_path.write_text(mgmt_network_xml(cfg))
    return xml_path


def list_networks(active_only: bool = False) -> list[str]:
    args = ["net-list", "--name"]
    if not active_only:
        args.append("--all")
    cp = virsh(*args, capture=True)
    return [n.strip() for n in cp.stdout.splitlines() if n.strip()]


def network_exists(name: str) -> bool:
    cp = virsh("net-info", name, capture=True, check=False)
    return cp.returncode == 0


def network_is_active(name: str) -> bool:
    cp = virsh("net-list", "--name", capture=True)
    return name in cp.stdout.splitlines()


def start_network(name: str) -> None:
    virsh("net-start", name, sudo=True)


def autostart_network(name: str) -> None:
    virsh("net-autostart", name, sudo=True)


def create_network_xml(
    name: str,
    mode: str,
    forward_dev: str | None = None,
    bridge_name: str | None = None,
    ip_address: str | None = None,
    netmask: str | None = None,
    dhcp_start: str | None = None,
    dhcp_end: str | None = None,
) -> str:
    lines = ["<network>"]
    lines.append(f"  <name>{name}</name>")

    if mode == "bridge":
        lines.append(f"  <forward mode='bridge'/>")
        lines.append(f"  <bridge name='{bridge_name or name}' stp='on' delay='0'/>")
    elif mode == "nat":
        lines.append(f"  <forward mode='nat'>")
        if forward_dev:
            lines.append(f"    <interface dev='{forward_dev}'/>")
        lines.append("  </forward>")
        lines.append(f"  <bridge name='virbr-{name[:8]}' stp='on' delay='0'/>")
    else:
        lines.append(f"  <bridge name='virbr-{name[:8]}' stp='on' delay='0'/>")

    if ip_address and netmask:
        lines.append(f"  <ip address='{ip_address}' netmask='{netmask}'>")
        if dhcp_start and dhcp_end:
            lines.append("    <dhcp>")
            lines.append(f"      <range start='{dhcp_start}' end='{dhcp_end}'/>")
            lines.append("    </dhcp>")
        lines.append("  </ip>")

    lines.append("</network>")
    return "\n".join(lines)


def define_network(xml_path: Path) -> None:
    virsh("net-define", str(xml_path), sudo=True)


def random_mac() -> str:
    return "52:54:%02x:%02x:%02x:%02x" % tuple(randint(0, 255) for _ in range(4))


def get_vm_ip_addresses(name: str) -> list[dict[str, str]]:
    """Query VM IP addresses via guest agent, DHCP lease, or ARP table."""
    for source in ("agent", "lease", "arp"):
        cp = virsh("domifaddr", name, "--source", source, capture=True, check=False)
        if cp.returncode != 0:
            continue
        addresses: list[dict[str, str]] = []
        for line in cp.stdout.splitlines():
            line = line.strip()
            if not line or line.startswith("Name") or line.startswith("-"):
                continue
            parts = line.split()
            if len(parts) >= 4:
                ip = parts[3].split("/")[0]
                # Skip loopback, not-yet-assigned placeholders, and invalid tokens
                if ip in ("127.0.0.1", "::1", "N/A", "N") or ("." not in ip and ":" not in ip):
                    continue
                addresses.append(
                    {
                        "iface": parts[0],
                        "mac": parts[1],
                        "protocol": parts[2],
                        "ip": ip,
                    }
                )
        if addresses:
            return addresses
    return []


def get_vm_interfaces(name: str) -> dict[str, str]:
    addresses = get_vm_ip_addresses(name)
    return {addr["iface"]: addr["ip"] for addr in addresses if addr.get("ip")}


def get_vm_wan_ip(name: str) -> str | None:
    interfaces = get_vm_interfaces(name)
    for iface, ip in interfaces.items():
        if ip.startswith("192.168.") or (
            ip.startswith("10.") and not ip.startswith("10.31.")
        ):
            return ip
    for iface, ip in interfaces.items():
        if ip:
            return ip
    return None


def get_vm_state(name: str) -> str:
    cp = virsh("domstate", name, capture=True, check=False)
    return cp.stdout.strip() if cp.returncode == 0 else ""


def vm_exists(name: str) -> bool:
    cp = virsh("dominfo", name, capture=True, check=False)
    return cp.returncode == 0


def start_vm_libvirt(name: str) -> None:
    virsh("start", name)


def stop_vm_libvirt(name: str) -> None:
    virsh("destroy", name, check=False)


def resume_vm_libvirt(name: str) -> None:
    virsh("resume", name)


def undefine_vm_libvirt(name: str) -> None:
    virsh("undefine", name, check=False)


from .config import Config
