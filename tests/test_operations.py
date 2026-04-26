from __future__ import annotations

import datetime
from unittest.mock import MagicMock, patch

import pytest

from latita.operations import (
    _deep_update,
    _osinfo_for_recipe,
    _package_manager_for_recipe,
    _check_ephemeral_constraints,
    build_recipe,
    normalize_template,
)


class TestNormalizeTemplate:
    def test_headless_defaults(self):
        data = {"profile": "headless"}
        norm = normalize_template(data)
        assert norm["profile"] == "headless"
        assert norm["ephemeral"]["transient"] is True
        assert norm["cpus"] == 2
        assert norm["memory"] == 4096
        assert norm["network"]["mode"] == "nat"

    def test_desktop_defaults(self):
        data = {"profile": "desktop"}
        norm = normalize_template(data)
        assert norm["profile"] == "desktop"
        assert norm["ephemeral"]["transient"] is False

    def test_invalid_profile(self):
        with pytest.raises(Exception):
            normalize_template({"profile": "invalid"})

    def test_ephemeral_parsing(self):
        data = {
            "profile": "headless",
            "ephemeral": {"max_runs": 5, "expires_after_hours": 24},
        }
        norm = normalize_template(data)
        assert norm["ephemeral"]["max_runs"] == 5
        assert norm["ephemeral"]["expires_after_hours"] == 24

    def test_security_defaults(self):
        data = {"profile": "headless"}
        norm = normalize_template(data)
        assert norm["security"]["selinux"] is True
        assert norm["security"]["no_guest_agent"] is True
        assert norm["security"]["restrict_network"] is False

    def test_nat_network_default(self):
        data = {"profile": "headless"}
        norm = normalize_template(data)
        assert norm["network"]["mode"] == "nat"

    def test_isolated_network_override(self):
        data = {"profile": "headless", "network": {"mode": "isolated"}}
        norm = normalize_template(data)
        assert norm["network"]["mode"] == "isolated"


class TestDeepUpdate:
    def test_nested_merge(self):
        base = {"a": 1, "b": {"c": 2}}
        _deep_update(base, {"b": {"d": 3}})
        assert base["b"]["c"] == 2
        assert base["b"]["d"] == 3

    def test_override_scalar(self):
        base = {"a": 1}
        _deep_update(base, {"a": 2})
        assert base["a"] == 2


class TestOsinfo:
    def test_fedora(self):
        assert "fedora" in _osinfo_for_recipe({"os_family": "fedora", "base_image": ""})

    def test_ubuntu(self):
        assert "ubuntu" in _osinfo_for_recipe({"os_family": "ubuntu", "base_image": ""})

    def test_debian(self):
        assert "debian" in _osinfo_for_recipe({"os_family": "debian", "base_image": ""})

    def test_fallback(self):
        assert "linux2024" in _osinfo_for_recipe({"os_family": "alpine", "base_image": ""})

    def test_extract_version_from_base_image(self):
        info = _osinfo_for_recipe({"os_family": "fedora", "base_image": "fedora42-base.qcow2"})
        assert "fedora42" in info


class TestPackageManager:
    def test_fedora(self):
        assert _package_manager_for_recipe({"os_family": "fedora"}) == "dnf"

    def test_ubuntu(self):
        assert _package_manager_for_recipe({"os_family": "ubuntu"}) == "apt"

    def test_debian(self):
        assert _package_manager_for_recipe({"os_family": "debian"}) == "apt"

    def test_alpine(self):
        assert _package_manager_for_recipe({"os_family": "alpine"}) == "apk"


class TestEphemeralConstraints:
    def test_expired_vm(self, isolated_config):
        from latita.metadata import write_instance_spec
        past = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=1)).isoformat()
        write_instance_spec("expiredvm", {"expire_at": past})
        with pytest.raises(Exception):
            _check_ephemeral_constraints("expiredvm")

    def test_max_runs_reached(self, isolated_config):
        from latita.metadata import write_instance_spec
        write_instance_spec("maxvm", {"max_runs": 2, "run_count": 2})
        with pytest.raises(Exception):
            _check_ephemeral_constraints("maxvm")

    def test_no_constraints(self, isolated_config):
        from latita.metadata import write_instance_spec
        write_instance_spec("freevm", {})
        # Should not raise
        _check_ephemeral_constraints("freevm")


class TestBuildRecipe:
    def test_uses_template_defaults(self, isolated_config):
        recipe = build_recipe("headless")
        assert recipe["profile"] == "headless"
        assert recipe["template_name"] == "headless"

    def test_overrides_applied(self, isolated_config):
        recipe = build_recipe("headless", overrides={"cpus": 16})
        assert recipe["cpus"] == 16

    def test_capsules_resolved(self, isolated_config):
        recipe = build_recipe("headless", capsule_names=["podman-host"])
        assert "podman-host" in recipe["capsules"]
        assert "_resolved_capsules" in recipe

    def test_keys_populated(self, isolated_config):
        recipe = build_recipe("headless")
        assert "_keys" in recipe
        assert recipe["_keys"]["host_pubkey_path"]

    def test_capsule_dependencies_resolved(self, isolated_config):
        recipe = build_recipe("headless", capsule_names=["code-server"])
        resolved = recipe["_resolved_capsules"]
        descriptions = [c.get("description", "").lower() for c in resolved]
        assert any("podman" in d for d in descriptions)
        assert any("code-server" in d for d in descriptions)

    def test_capsule_dependency_order_in_recipe(self, isolated_config):
        recipe = build_recipe("headless", capsule_names=["code-server"])
        resolved = recipe["_resolved_capsules"]
        descriptions = [c.get("description", "").lower() for c in resolved]
        podman_idx = next(i for i, d in enumerate(descriptions) if "podman" in d)
        code_idx = next(i for i, d in enumerate(descriptions) if "code-server" in d)
        assert podman_idx < code_idx

    def test_user_data_includes_capsule_provisions(self, isolated_config):
        from latita.cloudinit import build_user_data
        from latita.operations import _osinfo_for_recipe, _package_manager_for_recipe

        recipe = build_recipe("headless", capsule_names=["podman-host"])
        keys = recipe["_keys"]
        user_data = build_user_data(
            profile=recipe["profile"],
            guest_user=recipe["guest_user"],
            host_pubkey="fake-host-key",
            lab_pubkey="fake-lab-key",
            lab_privkey=None,
            login_hash="",
            provision=recipe["provision"],
            capsule_provisions=[
                c.get("provision", {}) for c in recipe.get("_resolved_capsules", [])
            ],
            passwordless_sudo=recipe["passwordless_sudo"],
            package_manager=_package_manager_for_recipe(recipe),
        )
        assert "podman" in user_data


class TestDiscoverLatestFedoraUrl:
    @patch("latita.operations.urllib.request.urlopen")
    def test_returns_latest_from_html(self, mock_urlopen):
        html = (
            '<tr><td><a href="Fedora-Cloud-Base-Generic-43-1.3.x86_64.qcow2">file</a></td></tr>'
            '<tr><td><a href="Fedora-Cloud-Base-Generic-43-1.6.x86_64.qcow2">file</a></td></tr>'
        )
        mock_resp = MagicMock()
        mock_resp.read.return_value = html.encode()
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        from latita.operations import _discover_latest_fedora_url

        result = _discover_latest_fedora_url(
            "https://download.fedoraproject.org/pub/fedora/linux/releases/43/Cloud/x86_64/images/Fedora-Cloud-Base-Generic-43-1.3.x86_64.qcow2"
        )
        assert result is None


class TestDesktopVideoModel:
    """Verify desktop VMs use virtio video in session mode, qxl in system mode."""

    def test_session_mode_uses_virtio(self):
        from latita.operations import _run_create
        import inspect
        source = inspect.getsource(_run_create)
        assert 'cfg.is_session' in source
        assert '"--video", "virtio"' in source
        assert '"--video", "qxl"' in source

    def test_system_mode_uses_qxl(self):
        from latita.operations import _run_create
        import inspect
        source = inspect.getsource(_run_create)
        assert '"--video", "qxl"' in source
        assert '"--channel", "spicevmc"' in source

    @patch("latita.operations.urllib.request.urlopen")
    def test_returns_none_on_network_error(self, mock_urlopen):
        mock_urlopen.side_effect = Exception("connection refused")

        from latita.operations import _discover_latest_fedora_url

        result = _discover_latest_fedora_url(
            "https://download.fedoraproject.org/pub/fedora/linux/releases/43/Cloud/x86_64/images/Fedora-Cloud-Base-Generic-43-1.3.x86_64.qcow2"
        )
        assert result is None


class TestFindFreePort:
    """Verify _find_free_port uses bind() so it finds truly available ports."""

    def test_uses_bind_not_connect_ex(self):
        from unittest.mock import MagicMock
        from latita.operations import _find_free_port
        import socket

        with patch("latita.operations.socket.socket") as mock_socket_cls:
            mock_sock = MagicMock()
            mock_socket_cls.return_value = mock_sock
            mock_sock.__enter__ = MagicMock(return_value=mock_sock)
            mock_sock.__exit__ = MagicMock(return_value=False)

            def side_effect(addr):
                ip, port = addr
                if port == 2222:
                    raise OSError("Address already in use")
                return None

            mock_sock.bind.side_effect = side_effect
            port = _find_free_port()
            assert port == 2223
            # bind should have been called, not connect_ex
            assert mock_sock.bind.called
            assert not mock_sock.connect_ex.called

    def test_skips_busy_port(self):
        from unittest.mock import MagicMock
        from latita.operations import _find_free_port
        import socket

        with patch("latita.operations.socket.socket") as mock_socket_cls:
            mock_sock = MagicMock()
            mock_socket_cls.return_value = mock_sock
            mock_sock.__enter__ = MagicMock(return_value=mock_sock)
            mock_sock.__exit__ = MagicMock(return_value=False)

            call_count = 0

            def side_effect(addr):
                nonlocal call_count
                call_count += 1
                ip, port = addr
                if port < 2225:
                    raise OSError("Address already in use")
                return None

            mock_sock.bind.side_effect = side_effect
            port = _find_free_port()
            assert port == 2225
            assert call_count == 4  # 2222, 2223, 2224 failed; 2225 succeeded
