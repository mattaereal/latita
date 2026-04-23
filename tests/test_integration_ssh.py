from __future__ import annotations

import subprocess
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from latita.config import Config, get_config, set_config
from latita.libvirt import get_vm_ip_addresses, get_vm_state
from latita.metadata import read_instance_env, read_instance_recipe
from latita.operations import (
    apply_capsule_live,
    create_instance,
    destroy_instance,
    get_vm_ip,
    ssh_instance,
    start_instance,
    stop_instance,
)
from latita.utils import create_lab_key

FEDORA_IMG = Path("/home/matta/latita-vault/vm/base/fedora43-base.qcow2")
FEDORA_MIN_SIZE = 500_000_000  # ~500 MB


def _wait_for_vm_ip(name: str, timeout: int = 180) -> str:
    """Poll get_vm_ip_addresses until a valid dynamic IP is discovered or timeout."""
    start = time.time()
    while time.time() - start < timeout:
        addresses = get_vm_ip_addresses(name)
        if addresses:
            ip = addresses[0]["ip"]
            if "." in ip or ":" in ip:
                return ip
        time.sleep(3)
    raise TimeoutError(f"No dynamic IP discovered for {name} after {timeout}s")


@pytest.fixture
def fedora_cfg(isolated_config):
    """Override the isolated config with a Fedora 43 base image symlink."""
    cfg = isolated_config
    base = cfg.base_dir / cfg.default_base_name
    if not FEDORA_IMG.exists() or FEDORA_IMG.stat().st_size < FEDORA_MIN_SIZE:
        pytest.skip("Fedora 43 base image not available or incomplete")
    base.symlink_to(FEDORA_IMG)

    # Seed SSH keys
    create_lab_key("lab1")

    # Create a test capsule in the isolated config
    capsule_dir = cfg.root_dir / "capsules"
    capsule_dir.mkdir(parents=True, exist_ok=True)
    (capsule_dir / "test-echo.cap").write_text(
        """description: Test capsule for integration tests
compatibility:
  profiles: [headless, desktop]
  os_family: [fedora]
live:
  user: dev
  commands:
    - echo "capsule-live-test"
"""
    )

    yield cfg

    # Cleanup: destroy any lingering test domains
    try:
        cp = subprocess.run(
            ["virsh", "-c", cfg.libvirt_uri, "list", "--all", "--name"],
            capture_output=True, text=True, check=False,
        )
        for name in cp.stdout.splitlines():
            name = name.strip()
            if name.startswith("test-f43-"):
                subprocess.run(
                    ["virsh", "-c", cfg.libvirt_uri, "destroy", name],
                    capture_output=True, check=False,
                )
                subprocess.run(
                    ["virsh", "-c", cfg.libvirt_uri, "undefine", name],
                    capture_output=True, check=False,
                )
    except Exception:
        pass

    # Cleanup instance directory
    inst = cfg.inst_dir
    if inst.exists():
        for d in inst.iterdir():
            if d.is_dir() and d.name.startswith("test-f43-"):
                import shutil
                shutil.rmtree(d)


@pytest.mark.slow
class TestFedoraSsh:
    def _create_fedora_vm(self, cfg: Config, name: str):
        overrides = {
            "base_image": cfg.default_base_name,
            "ephemeral": {"transient": False, "destroy_on_stop": False},
            "memory": 2048,
            "cpus": 2,
            "disk_size": "10G",
            "network": {"mode": "user"},
            "security": {"no_guest_agent": False},
        }
        create_instance("headless", name=name, overrides=overrides)

    def test_dynamic_ip_discovery(self, fedora_cfg):
        """get_vm_ip discovers a real dynamic IP via the guest agent, not the static mgmt_ip."""
        cfg = fedora_cfg
        name = "test-f43-ip"
        self._create_fedora_vm(cfg, name)
        start_instance(name)

        ip = _wait_for_vm_ip(name, timeout=180)
        # The discovered IP should not be the static template IP (10.31.0.10)
        assert ip
        assert ip != "10.31.0.10"

        # Also verify get_vm_ip_addresses returned it
        addresses = get_vm_ip_addresses(name)
        assert any(a["ip"] == ip for a in addresses)

        destroy_instance(name)

    def test_ssh_instance_builds_correct_command(self, fedora_cfg):
        """ssh_instance constructs the right SSH command with dynamic IP and injected key."""
        cfg = fedora_cfg
        name = "test-f43-ssh"
        self._create_fedora_vm(cfg, name)
        start_instance(name)

        ip = _wait_for_vm_ip(name, timeout=180)

        # Only mock the actual SSH calls; let everything else (virsh, qemu-img) run for real
        real_run = subprocess.run

        def _side_effect(*args, **kwargs):
            cmd = args[0] if args else kwargs.get("args", [])
            if isinstance(cmd, list) and len(cmd) > 0 and cmd[0] == "ssh":
                return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
            return real_run(*args, **kwargs)

        with patch("latita.operations.subprocess.run", side_effect=_side_effect) as mock_run:
            ssh_instance(name, command="echo hello")

            ssh_calls = [c for c in mock_run.call_args_list if c.args and len(c.args[0]) > 0 and c.args[0][0] == "ssh"]
            assert len(ssh_calls) == 1
            cmd = ssh_calls[0].args[0]
            assert cmd[0] == "ssh"
            assert any(ip in a for a in cmd)
            assert any("lab1_ed25519" in a for a in cmd)
            assert "echo hello" in cmd

        destroy_instance(name)

    def test_apply_capsule_live_builds_correct_commands(self, fedora_cfg):
        """apply_capsule_live builds the right SSH command sequence for a capsule."""
        cfg = fedora_cfg
        name = "test-f43-capsule"
        self._create_fedora_vm(cfg, name)
        start_instance(name)

        ip = _wait_for_vm_ip(name, timeout=180)

        real_run = subprocess.run

        def _side_effect(*args, **kwargs):
            cmd = args[0] if args else kwargs.get("args", [])
            if isinstance(cmd, list) and len(cmd) > 0 and cmd[0] == "ssh":
                return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
            return real_run(*args, **kwargs)

        with patch("latita.operations.subprocess.run", side_effect=_side_effect) as mock_run:
            apply_capsule_live(name, "test-echo")

            ssh_calls = [c for c in mock_run.call_args_list if c.args and len(c.args[0]) > 0 and c.args[0][0] == "ssh"]
            assert len(ssh_calls) >= 1
            cmd = ssh_calls[0].args[0]
            assert cmd[0] == "ssh"
            assert any(ip in a for a in cmd)
            assert "capsule-live-test" in str(cmd)

        destroy_instance(name)

    def test_ensure_running_auto_starts_stopped_vm(self, fedora_cfg):
        """_ensure_running starts a stopped VM so it can be reached again."""
        from latita.cli import _ensure_running

        cfg = fedora_cfg
        name = "test-f43-autostart"
        self._create_fedora_vm(cfg, name)
        start_instance(name)

        # Wait until the guest agent reports an IP (VM is fully booted)
        _wait_for_vm_ip(name, timeout=180)

        # Stop the VM
        stop_instance(name)
        assert get_vm_state(name) != "running"

        # _ensure_running should start it
        _ensure_running(name)
        assert get_vm_state(name) == "running"

        # After auto-start, the guest agent should eventually report an IP again
        ip = _wait_for_vm_ip(name, timeout=180)
        assert ip
        assert ip != "10.31.0.10"

        destroy_instance(name)


def _wait_for_ssh_ready(name: str, timeout: int = 300) -> str:
    """Poll until SSH accepts connections, returning the VM IP."""
    env = read_instance_env(name)
    user = env.get("GUEST_USER", "dev")
    recipe = read_instance_recipe(name)
    key = None
    if recipe:
        keys = recipe.get("_keys", {})
        lab_priv = keys.get("lab_privkey_path")
        if lab_priv and Path(lab_priv).exists():
            key = lab_priv
    if not key:
        raise RuntimeError("No SSH private key found")

    start = time.time()
    ip = None
    while time.time() - start < timeout:
        try:
            ip = _wait_for_vm_ip(name, timeout=10)
        except TimeoutError:
            time.sleep(3)
            continue
        cmd = [
            "ssh",
            "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null",
            "-o", "ConnectTimeout=5",
            "-o", "BatchMode=yes",
            "-i", key,
            f"{user}@{ip}",
            "echo ssh-ready",
        ]
        cp = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if cp.returncode == 0 and "ssh-ready" in cp.stdout:
            return ip
        time.sleep(3)
    raise TimeoutError(f"SSH not ready for {name} after {timeout}s")


def _ssh_run(name: str, command: str, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    """Run a command on a VM via SSH using the injected lab key."""
    env = read_instance_env(name)
    user = env.get("GUEST_USER", "dev")
    recipe = read_instance_recipe(name)
    key = None
    if recipe:
        keys = recipe.get("_keys", {})
        lab_priv = keys.get("lab_privkey_path")
        if lab_priv and Path(lab_priv).exists():
            key = lab_priv
    if not key:
        raise RuntimeError("No SSH private key found")

    ip = _wait_for_ssh_ready(name, timeout=300)

    cmd = [
        "ssh",
        "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=/dev/null",
        "-o", "ConnectTimeout=10",
        "-o", "BatchMode=yes",
        "-i", key,
        f"{user}@{ip}",
        command,
    ]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


@pytest.mark.slow
class TestCapsuleProvision:
    """Test capsules applied at VM creation time (cloud-init provision path).

    NOTE: Real SSH into session-mode VMs with type=user (SLIRP) networking
    is not possible from the host — the VM's internal IP (e.g. 10.0.2.15)
    is not routable. These tests verify provisioning by inspecting the
    generated cloud-init user-data.yaml and by checking VM state.
    """

    def _create_fedora_vm_with_capsule(self, cfg: Config, name: str, capsule_names: list[str]):
        overrides = {
            "base_image": cfg.default_base_name,
            "ephemeral": {"transient": False, "destroy_on_stop": False},
            "memory": 2048,
            "cpus": 2,
            "disk_size": "10G",
            "network": {"mode": "user"},
            "security": {"no_guest_agent": False},
            "capsules": capsule_names,
        }
        create_instance("headless", name=name, overrides=overrides)

    def test_podman_host_user_data_contains_packages(self, fedora_cfg):
        """VM created with podman-host has podman packages in cloud-init user-data."""
        cfg = fedora_cfg
        name = "test-f43-podman-ud"
        self._create_fedora_vm_with_capsule(cfg, name, ["podman-host"])
        ud_path = cfg.inst_dir / name / "user-data.yaml"
        assert ud_path.exists()
        ud = ud_path.read_text()
        assert "podman" in ud
        assert "slirp4netns" in ud
        assert "loginctl enable-linger" in ud
        destroy_instance(name)

    def test_code_server_user_data_has_dependency_commands(self, fedora_cfg):
        """VM created with code-server includes podman-host provisions in user-data."""
        cfg = fedora_cfg
        name = "test-f43-cs-ud"
        self._create_fedora_vm_with_capsule(cfg, name, ["code-server"])
        ud_path = cfg.inst_dir / name / "user-data.yaml"
        assert ud_path.exists()
        ud = ud_path.read_text()
        # podman-host provisions
        assert "podman" in ud
        assert "loginctl enable-linger" in ud
        # code-server provisions
        assert "code-server" in ud
        assert "ghcr.io/coder/code-server" in ud
        # Dependency should come before dependent (podman setup before container run)
        podman_idx = ud.index("podman")
        cs_idx = ud.index("code-server")
        assert podman_idx < cs_idx
        destroy_instance(name)

    def test_ai_agents_user_data_contains_nodejs(self, fedora_cfg):
        """VM created with ai-agents capsule has Node.js packages in user-data."""
        cfg = fedora_cfg
        name = "test-f43-ai-ud"
        self._create_fedora_vm_with_capsule(cfg, name, ["ai-agents"])
        ud_path = cfg.inst_dir / name / "user-data.yaml"
        assert ud_path.exists()
        ud = ud_path.read_text()
        assert "nodejs" in ud or "npm" in ud
        assert "claude-code" in ud or "anthropic" in ud
        assert "kimi-cli" in ud or "kimi" in ud
        destroy_instance(name)

    @pytest.mark.skip(reason="Host-to-VM SSH requires NAT bridge or port forwarding; not available in qemu:///session mode")
    def test_real_ssh_to_vm_skipped_in_session_mode(self, fedora_cfg):
        """Placeholder: real SSH end-to-end tests need qemu:///system or port forwarding."""
        pass
