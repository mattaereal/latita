from __future__ import annotations

import datetime

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
