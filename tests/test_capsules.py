from __future__ import annotations

import pytest

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
        # code-server exists as builtin
        resolved = resolve_capsules(["code-server"], profile="headless", os_family="fedora")
        assert len(resolved) == 1

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
