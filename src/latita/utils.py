from __future__ import annotations

import getpass
import ipaddress
import shutil
import subprocess
from pathlib import Path
from typing import Any

import typer


def need_cmd(*names: str) -> None:
    missing = [n for n in names if shutil.which(n) is None]
    if missing:
        raise typer.BadParameter(f"missing commands: {', '.join(missing)}")


def run(
    cmd: list[str],
    check: bool = True,
    capture: bool = False,
    input_text: str | None = None,
    sudo: bool = False,
) -> subprocess.CompletedProcess[str]:
    full = ["sudo", *cmd] if sudo else cmd
    return subprocess.run(
        full,
        check=check,
        text=True,
        capture_output=capture,
        input=input_text,
    )


def log_cmd(cmd: list[str]) -> None:
    from .ui import console

    console.print(f"[dim]> {' '.join(cmd)}[/dim]")


def validate_name(value: str) -> str:
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_")
    if not value or any(c not in allowed for c in value):
        raise typer.BadParameter("name must contain only letters, numbers, dash, underscore")
    return value


def validate_ip(value: str) -> str:
    try:
        ipaddress.ip_address(value)
    except ValueError:
        raise typer.BadParameter(f"invalid IP: {value}")
    return value


def validate_cpus(value: str) -> int:
    try:
        v = int(value)
        if v < 1 or v > 256:
            raise ValueError
        return v
    except ValueError:
        raise typer.BadParameter("cpus must be an integer between 1 and 256")


def validate_memory(value: str) -> int:
    try:
        v = int(value)
        if v < 256 or v > 2_000_000:
            raise ValueError
        return v
    except ValueError:
        raise typer.BadParameter("memory must be an integer between 256 MiB and ~2 TiB")


def validate_disk_size(value: str) -> str:
    value = value.strip().upper()
    if not value:
        raise typer.BadParameter("disk size required")
    if value[-1] not in "GMKT":
        raise typer.BadParameter("disk size must end with G, M, K, or T")
    try:
        int(value[:-1])
    except ValueError:
        raise typer.BadParameter("disk size must be a number followed by G/M/K/T")
    return value


def default_host_pubkey() -> Path | None:
    for p in (
        Path.home() / ".ssh/id_ed25519.pub",
        Path.home() / ".ssh/id_ecdsa.pub",
        Path.home() / ".ssh/id_rsa.pub",
    ):
        if p.exists():
            return p
    return None


def host_key_exists() -> bool:
    return default_host_pubkey() is not None


def create_host_key() -> Path:
    key = Path.home() / ".ssh/id_ed25519"
    if key.exists():
        return key
    need_cmd("ssh-keygen")
    run(
        [
            "ssh-keygen",
            "-q",
            "-t",
            "ed25519",
            "-N",
            "",
            "-C",
            "latita-host",
            "-f",
            str(key),
        ]
    )
    key.chmod(0o600)
    key.with_suffix(".pub").chmod(0o644)
    return key


def lab_key_exists(lab: str = "lab1") -> bool:
    from .config import get_config

    return (get_config().keys_dir / f"{lab}_ed25519.pub").exists()


def create_lab_key(lab: str = "lab1") -> Path:
    from .config import get_config

    key = get_config().keys_dir / f"{lab}_ed25519"
    if key.exists():
        return key
    need_cmd("ssh-keygen")
    get_config().keys_dir.mkdir(parents=True, exist_ok=True)
    run(
        [
            "ssh-keygen",
            "-q",
            "-t",
            "ed25519",
            "-N",
            "",
            "-C",
            f"latita-{lab}",
            "-f",
            str(key),
        ]
    )
    key.chmod(0o600)
    key.with_suffix(".pub").chmod(0o644)
    return key


def hash_password_interactive(guest_user: str = "dev") -> str:
    need_cmd("openssl")
    while True:
        pw1 = getpass.getpass(f"Password for VM user '{guest_user}' (not your host password): ")
        pw2 = getpass.getpass("Repeat password: ")
        if pw1 != pw2:
            print("passwords do not match")
            continue
        if not pw1:
            print("password required")
            continue
        return hash_password(pw1)


def hash_password(password: str) -> str:
    """Hash a plaintext password with openssl SHA-512 (non-interactive)."""
    need_cmd("openssl")
    cp = run(["openssl", "passwd", "-6", "-stdin"], capture=True, input_text=password + "\n")
    return cp.stdout.strip()


def read_text(path: Path) -> str:
    return path.expanduser().read_text().strip()


def shred_file(path: Path, passes: int = 3) -> None:
    if not path.exists():
        return
    try:
        if shutil.which("shred"):
            # Capture output so permission errors don't spam the terminal
            run(["shred", "-n", str(passes), "-u", str(path)], check=False, capture=True)
        else:
            path.unlink()
    except PermissionError:
        # May happen for root-owned files in user dirs (system libvirt)
        pass


def parse_iso_datetime(value: str) -> Any:
    from datetime import datetime, timezone

    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
