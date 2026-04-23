from __future__ import annotations

import pytest

from latita.config import list_capsules, load_capsule
from latita.capsules import (
    check_capsule_compatibility,
    capsule_live_commands,
    capsule_live_user,
    capsule_provision_fragment,
    list_compatible_capsules,
    merge_provision_fragments,
    resolve_capsules,
)


class TestCompatibility:
    def test_no_restrictions(self):
        cap = {"description": "open"}
        ok, reason = check_capsule_compatibility(cap, profile="headless", os_family="fedora")
        assert ok is True
        assert reason == ""

    def test_profile_mismatch(self):
        cap = {"compatibility": {"profiles": ["desktop"]}}
        ok, reason = check_capsule_compatibility(cap, profile="headless")
        assert ok is False
        assert "profile" in reason

    def test_os_family_mismatch(self):
        cap = {"compatibility": {"os_family": ["ubuntu"]}}
        ok, reason = check_capsule_compatibility(cap, os_family="fedora")
        assert ok is False
        assert "os_family" in reason

    def test_profile_match(self):
        cap = {"compatibility": {"profiles": ["headless", "desktop"]}}
        ok, _ = check_capsule_compatibility(cap, profile="headless")
        assert ok is True


class TestProvisionMerging:
    def test_merge_packages(self):
        base = {"packages": ["git"], "root_commands": [], "user_commands": [], "write_files": []}
        frag = {"packages": ["git", "vim"], "root_commands": ["echo hi"], "user_commands": [], "write_files": []}
        merged = merge_provision_fragments(base, frag)
        assert merged["packages"] == ["git", "vim"]
        assert merged["root_commands"] == ["echo hi"]

    def test_dedupe_packages(self):
        base = {"packages": ["git"], "root_commands": [], "user_commands": [], "write_files": []}
        frag = {"packages": ["git"], "root_commands": [], "user_commands": [], "write_files": []}
        merged = merge_provision_fragments(base, frag)
        assert merged["packages"] == ["git"]

    def test_merge_write_files(self):
        base = {"packages": [], "root_commands": [], "user_commands": [], "write_files": [{"path": "/etc/a", "content": "a"}]}
        frag = {"packages": [], "root_commands": [], "user_commands": [], "write_files": [{"path": "/etc/b", "content": "b"}]}
        merged = merge_provision_fragments(base, frag)
        assert len(merged["write_files"]) == 2

    def test_write_file_overwrite_blocked(self):
        # Same path should not duplicate
        base = {"packages": [], "root_commands": [], "user_commands": [], "write_files": [{"path": "/etc/a", "content": "a"}]}
        frag = {"packages": [], "root_commands": [], "user_commands": [], "write_files": [{"path": "/etc/a", "content": "b"}]}
        merged = merge_provision_fragments(base, frag)
        assert len(merged["write_files"]) == 1


class TestLiveCommands:
    def test_extract_commands(self):
        cap = {"live": {"commands": ["echo 1", "echo 2"], "user": "admin"}}
        assert capsule_live_commands(cap) == ["echo 1", "echo 2"]
        assert capsule_live_user(cap) == "admin"

    def test_empty_live(self):
        cap = {}
        assert capsule_live_commands(cap) == []
        assert capsule_live_user(cap) == "dev"

    def test_provision_fragment(self):
        cap = {"provision": {"packages": ["vim"]}}
        assert capsule_provision_fragment(cap)["packages"] == ["vim"]


class TestResolve:
    def test_resolve_builtin(self):
        # code-server exists as builtin and depends on podman-host
        resolved = resolve_capsules(["code-server"], profile="headless", os_family="fedora")
        assert len(resolved) == 2  # podman-host + code-server

    def test_resolve_incompatible(self):
        with pytest.raises(Exception):
            resolve_capsules(["code-server"], profile="unknown", os_family="fedora")


class TestListCompatible:
    def test_list_all_when_no_filter(self):
        caps = list_compatible_capsules()
        assert "code-server" in caps

    def test_filter_by_profile(self):
        caps = list_compatible_capsules(profile="headless")
        assert "code-server" in caps


class TestDependsOn:
    def test_single_dependency_resolved(self):
        # code-server depends on podman-host
        resolved = resolve_capsules(["code-server"], profile="headless", os_family="fedora")
        names = [c.get("description", "") for c in resolved]
        # podman-host should come before code-server
        assert any("podman" in n.lower() for n in names)
        assert any("code-server" in n.lower() for n in names)

    def test_dependency_deduplication(self):
        # Requesting both podman-host and code-server should not duplicate podman-host
        resolved = resolve_capsules(
            ["podman-host", "code-server"],
            profile="headless",
            os_family="fedora",
        )
        descs = [c.get("description", "").lower() for c in resolved]
        # Both capsules mention "podman" in their descriptions; check that
        # podman-host itself appears only once
        host_count = sum(1 for d in descs if "podman-host" in d or "rootless containers" in d)
        assert host_count == 1
        # Total should be exactly 2 (podman-host + code-server)
        assert len(resolved) == 2

    def test_chain_dependency(self):
        # open-webui depends on ollama
        resolved = resolve_capsules(
            ["open-webui"],
            profile="headless",
            os_family="fedora",
        )
        descs = [c.get("description", "") for c in resolved]
        assert any("ollama" in d.lower() for d in descs)
        assert any("webui" in d.lower() for d in descs)

    def test_cycle_detection(self):
        # Create a fake cycle by temporarily mocking load_capsule
        from unittest.mock import patch

        def fake_load(name):
            return {
                "a": {"description": "A", "depends_on": ["b"]},
                "b": {"description": "B", "depends_on": ["a"]},
            }[name]

        with patch("latita.capsules.load_capsule", fake_load):
            with pytest.raises(Exception) as exc_info:
                resolve_capsules(["a"])
            assert "cycle" in str(exc_info.value).lower()

    def test_dependency_order(self):
        # Dependencies should appear before dependents in the resolved list
        resolved = resolve_capsules(
            ["code-server"],
            profile="headless",
            os_family="fedora",
        )
        descs = [c.get("description", "") for c in resolved]
        podman_idx = next(i for i, d in enumerate(descs) if "podman" in d.lower())
        code_idx = next(i for i, d in enumerate(descs) if "code-server" in d.lower())
        assert podman_idx < code_idx

    def test_ai_agents_resolves_all_deps(self):
        resolved = resolve_capsules(
            ["ai-agents"],
            profile="headless",
            os_family="fedora",
        )
        descs = [c.get("description", "").lower() for c in resolved]
        assert any("hermes" in d for d in descs)
        assert any("openclaw" in d for d in descs)
        assert any("all major ai" in d for d in descs)


class TestBuiltinCapsules:
    """Verify every built-in capsule loads, has valid structure, and resolves."""

    def test_all_builtin_capsules_load(self):
        names = list_capsules()
        assert len(names) >= 10
        for name in names:
            cap = load_capsule(name)
            assert "description" in cap, f"{name} missing description"
            # Should not crash when extracting fragments
            _ = capsule_provision_fragment(cap)
            _ = capsule_live_commands(cap)

    def test_all_capsules_resolve_individually(self):
        for name in list_capsules():
            resolved = resolve_capsules([name], profile="headless", os_family="fedora")
            assert len(resolved) >= 1
            # Verify dependency order: dependencies come before dependents
            if len(resolved) > 1:
                descs = [c.get("description", "") for c in resolved]
                for i, cap in enumerate(resolved):
                    deps = cap.get("depends_on", [])
                    if isinstance(deps, str):
                        deps = [deps]
                    for dep in deps:
                        dep_cap = load_capsule(dep)
                        dep_desc = dep_cap.get("description", "")
                        dep_idx = next(
                            (j for j, d in enumerate(descs) if d == dep_desc),
                            None,
                        )
                        assert dep_idx is not None
                        assert dep_idx < i, f"{name}: dependency {dep} not before {i}"

    def test_all_capsules_are_compatible_with_headless_fedora(self):
        for name in list_capsules():
            cap = load_capsule(name)
            ok, reason = check_capsule_compatibility(cap, profile="headless", os_family="fedora")
            assert ok, f"{name} incompatible with headless/fedora: {reason}"
