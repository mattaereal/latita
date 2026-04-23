from __future__ import annotations

from latita.hardening import (
    SecurityProfile,
    build_nwfilter_xml,
    build_no_agent_args,
    build_selinux_context_args,
    apply_hardening_to_args,
)


class TestSecurityProfile:
    def test_defaults(self):
        p = SecurityProfile()
        assert p.selinux is True
        assert p.no_guest_agent is True
        assert p.nwfilter_drop_all is False
        assert p.allow_hosts == []

    def test_from_dict(self):
        p = SecurityProfile.from_dict({"selinux": False, "allow_hosts": ["1.2.3.4"]})
        assert p.selinux is False
        assert p.allow_hosts == ["1.2.3.4"]

    def test_roundtrip(self):
        p = SecurityProfile(allow_hosts=["github.com"])
        d = p.to_dict()
        p2 = SecurityProfile.from_dict(d)
        assert p2.allow_hosts == ["github.com"]


class TestNwFilterXml:
    def test_permissive_mode(self):
        xml = build_nwfilter_xml("test", allow_hosts=[], drop_all=False)
        assert "<tcp/>" in xml
        assert "<udp/>" in xml
        assert "drop" not in xml

    def test_drop_all_mode(self):
        xml = build_nwfilter_xml("test", allow_hosts=[], drop_all=True)
        assert "action='drop'" in xml
        assert "<tcp/>" not in xml
        assert "<udp/>" not in xml

    def test_allow_hosts_blocks_generic_tcp(self):
        xml = build_nwfilter_xml("test", allow_hosts=["1.2.3.4", "5.6.7.8"], drop_all=False)
        assert "<tcp/>" not in xml
        assert "<udp/>" not in xml
        assert "dstipaddr='1.2.3.4'" in xml
        assert "dstipaddr='5.6.7.8'" in xml
        assert "action='drop'" in xml

    def test_allow_hosts_priority_order(self):
        xml = build_nwfilter_xml("test", allow_hosts=["1.2.3.4"])
        # allow rules should be priority 50, drop at 200
        assert "priority='50'" in xml
        assert "priority='200'" in xml


class TestNoAgentArgs:
    def test_returns_channel_none(self):
        args = build_no_agent_args()
        assert args == ["--channel", "none"]


class TestSelinuxArgs:
    def test_returns_list(self):
        args = build_selinux_context_args()
        assert isinstance(args, list)


class TestApplyHardening:
    def test_no_guest_agent_injected(self):
        profile = SecurityProfile(no_guest_agent=True)
        args = apply_hardening_to_args(profile, ["--name", "vm"])
        assert "--channel" in args
        assert "none" in args

    def test_guest_agent_enabled(self):
        profile = SecurityProfile(no_guest_agent=False)
        args = apply_hardening_to_args(profile, ["--name", "vm"])
        assert "--channel" in args
        assert "org.qemu.guest_agent.0" in " ".join(args)

    def test_nwfilter_not_injected_when_no_restrictions(self):
        profile = SecurityProfile(nwfilter_drop_all=False, allow_hosts=[])
        args = apply_hardening_to_args(profile, ["--name", "vm"])
        assert "filterref" not in " ".join(args)
