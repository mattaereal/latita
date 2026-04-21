from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

from .utils import run


class SecurityProfile:
    """Security hardening options for QEMU/libvirt guests."""

    def __init__(
        self,
        selinux: bool = True,
        no_guest_agent: bool = True,
        nwfilter_drop_all: bool = False,
        allow_hosts: list[str] | None = None,
        readonly_root: bool = False,
    ) -> None:
        self.selinux = selinux
        self.no_guest_agent = no_guest_agent
        self.nwfilter_drop_all = nwfilter_drop_all
        self.allow_hosts = allow_hosts or []
        self.readonly_root = readonly_root

    def to_dict(self) -> dict[str, Any]:
        return {
            "selinux": self.selinux,
            "no_guest_agent": self.no_guest_agent,
            "nwfilter_drop_all": self.nwfilter_drop_all,
            "allow_hosts": self.allow_hosts,
            "readonly_root": self.readonly_root,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SecurityProfile:
        return cls(
            selinux=bool(data.get("selinux", True)),
            no_guest_agent=bool(data.get("no_guest_agent", True)),
            nwfilter_drop_all=bool(data.get("nwfilter_drop_all", False)),
            allow_hosts=list(data.get("allow_hosts", [])),
            readonly_root=bool(data.get("readonly_root", False)),
        )


def _selinux_available() -> bool:
    return Path("/sys/fs/selinux").exists() and shutil.which("getenforce") is not None


def _selinux_enforcing() -> bool:
    if not _selinux_available():
        return False
    try:
        result = subprocess.run(
            ["getenforce"], capture_output=True, text=True, check=False
        )
        return result.stdout.strip().upper() == "ENFORCING"
    except Exception:
        return False


def build_selinux_context_args() -> list[str]:
    """Return virt-install arguments for SELinux sVirt isolation."""
    if not _selinux_available():
        return []
    return []


def build_nwfilter_xml(name: str, allow_hosts: list[str], drop_all: bool = False) -> str:
    """Build libvirt nwfilter XML for outbound host filtering.

    Priority rules (libvirt evaluates lower numbers first):
      - 50:  per-host allow rules
      - 100: generic tcp/udp allow (only when NO host restrictions)
      - 200: default drop
    """
    lines = [f"<filter name='{name}-egress' chain='ipv4'>"]

    if allow_hosts:
        # Allow only specific destination IPs
        for host in allow_hosts:
            lines.append(
                f"  <rule action='accept' direction='out' priority='50'>"
                f"<all dstipaddr='{host}'/>"
                f"</rule>"
            )
        # Drop everything else
        lines.append("  <rule action='drop' direction='out' priority='200'><all/></rule>")
    elif drop_all:
        # Drop all outbound
        lines.append("  <rule action='drop' direction='out' priority='200'><all/></rule>")
    else:
        # Allow TCP and UDP broadly (legacy permissive mode)
        lines.append("  <rule action='accept' direction='out' priority='100'><tcp/></rule>")
        lines.append("  <rule action='accept' direction='out' priority='100'><udp/></rule>")

    lines.append("</filter>")
    return "\n".join(lines)


def build_no_agent_args() -> list[str]:
    """Return virt-install args that disable qemu-guest-agent channel."""
    return ["--channel", "none"]


def apply_hardening_to_args(
    profile: SecurityProfile, args: list[str], *, vm_name: str = ""
) -> list[str]:
    if profile.no_guest_agent:
        args = [*args, *build_no_agent_args()]
    if profile.nwfilter_drop_all or profile.allow_hosts:
        filter_name = f"{vm_name}-egress" if vm_name else "latita-egress"
        xml = build_nwfilter_xml(filter_name, profile.allow_hosts, drop_all=profile.nwfilter_drop_all)
        try:
            run(
                ["virsh", "nwfilter-define", "/dev/stdin"],
                input_text=xml,
                check=False,
                capture=True,
            )
            args = [*args, "--network", f"filterref={filter_name}"]
        except Exception:
            pass
    return args
