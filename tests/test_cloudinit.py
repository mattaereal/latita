from __future__ import annotations

import yaml

from latita.cloudinit import (
    _package_install_block,
    _user_definition,
    build_network_config,
    build_user_data,
)


class TestPackageInstallBlock:
    def test_dnf_block(self):
        lines = _package_install_block(["git", "vim"], "dnf")
        assert any("dnf" in line for line in lines)
        assert any("git" in line for line in lines)

    def test_apt_block(self):
        lines = _package_install_block(["git", "vim"], "apt")
        assert any("apt-get" in line for line in lines)
        assert any("DEBIAN_FRONTEND" in line for line in lines)

    def test_apk_block(self):
        lines = _package_install_block(["git", "vim"], "apk")
        assert any("apk add" in line for line in lines)

    def test_empty_packages(self):
        lines = _package_install_block([], "dnf")
        assert lines == []


class TestUserDefinition:
    def test_headless_passwordless(self):
        ctx = {"guest_user": "dev", "host_pubkey": "ssh-ed25519 AAA", "lab_pubkey": "ssh-ed25519 BBB", "login_hash": ""}
        user = _user_definition("headless", ctx, passwordless_sudo=True)
        assert user["name"] == "dev"
        assert user["sudo"] == "ALL=(ALL) NOPASSWD:ALL"
        assert len(user["ssh_authorized_keys"]) == 2
        assert "passwd" not in user

    def test_headless_with_password(self):
        ctx = {"guest_user": "dev", "host_pubkey": "ssh-ed25519 AAA", "lab_pubkey": "ssh-ed25519 BBB", "login_hash": "$6$hash"}
        user = _user_definition("headless", ctx, passwordless_sudo=False)
        assert user["sudo"] == "ALL=(ALL) ALL"
        assert user["passwd"] == "$6$hash"
        assert user["lock_passwd"] is False

    def test_desktop_without_password(self):
        ctx = {"guest_user": "dev", "host_pubkey": "ssh-ed25519 AAA", "lab_pubkey": "", "login_hash": ""}
        user = _user_definition("desktop", ctx)
        # Empty login_hash should NOT set passwd for desktop
        assert "passwd" not in user
        assert "lock_passwd" not in user

    def test_desktop_with_password(self):
        ctx = {"guest_user": "dev", "host_pubkey": "ssh-ed25519 AAA", "lab_pubkey": "", "login_hash": "$6$hash"}
        user = _user_definition("desktop", ctx)
        assert user["passwd"] == "$6$hash"
        assert user["lock_passwd"] is False


class TestBuildUserData:
    def test_generates_valid_yaml(self):
        ud = build_user_data(
            profile="headless",
            guest_user="dev",
            host_pubkey="ssh-ed25519 AAA",
            lab_pubkey="ssh-ed25519 BBB",
            package_manager="dnf",
        )
        assert ud.startswith("#cloud-config")
        data = yaml.safe_load(ud)
        assert data["ssh_pwauth"] is False
        assert len(data["users"]) == 1
        assert data["runcmd"]

    def test_apt_package_manager(self):
        ud = build_user_data(
            profile="headless",
            guest_user="dev",
            host_pubkey="ssh-ed25519 AAA",
            package_manager="apt",
            provision={"packages": ["curl"], "write_files": [], "root_commands": [], "user_commands": []},
        )
        assert "apt-get" in ud

    def test_capsule_provisions_merged(self):
        ud = build_user_data(
            profile="headless",
            guest_user="dev",
            host_pubkey="ssh-ed25519 AAA",
            capsule_provisions=[
                {"packages": ["vim"], "write_files": [], "root_commands": [], "user_commands": []}
            ],
        )
        data = yaml.safe_load(ud)
        write_files = data["write_files"]
        bootstrap = [f for f in write_files if f["path"].endswith("bootstrap-headless.sh")]
        assert bootstrap


class TestBuildNetworkConfig:
    def test_basic_structure(self):
        nc = build_network_config("52:54:00:00:00:01", "52:54:00:00:00:02", "10.31.0.10")
        data = yaml.safe_load(nc)
        assert data["version"] == 2
        assert data["ethernets"]["wan0"]["dhcp4"] is True
        assert data["ethernets"]["mgmt0"]["addresses"] == ["10.31.0.10/24"]

    def test_custom_prefix(self):
        nc = build_network_config("52:54:00:00:00:01", "52:54:00:00:00:02", "10.31.0.10", "16")
        data = yaml.safe_load(nc)
        assert data["ethernets"]["mgmt0"]["addresses"] == ["10.31.0.10/16"]
