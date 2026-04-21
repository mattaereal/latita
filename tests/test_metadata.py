from __future__ import annotations

from latita.metadata import (
    get_run_count,
    increment_run_count,
    read_instance_env,
    read_instance_recipe,
    read_instance_spec,
    write_instance_env,
    write_instance_recipe,
    write_instance_spec,
)


class TestJsonStorage:
    def test_write_read_recipe(self, isolated_config):
        write_instance_recipe("testvm", {"cpus": 2, "profile": "headless"})
        data = read_instance_recipe("testvm")
        assert data["cpus"] == 2
        assert data["profile"] == "headless"

    def test_read_missing_returns_empty(self, isolated_config):
        assert read_instance_recipe("nonexistent") == {}
        assert read_instance_spec("nonexistent") == {}

    def test_write_read_spec(self, isolated_config):
        write_instance_spec("testvm", {"run_count": 0, "transient": True})
        data = read_instance_spec("testvm")
        assert data["transient"] is True


class TestRunCount:
    def test_increment(self, isolated_config):
        write_instance_spec("testvm", {"run_count": 0})
        assert increment_run_count("testvm") == 1
        assert increment_run_count("testvm") == 2
        assert get_run_count("testvm") == 2

    def test_get_count_missing(self, isolated_config):
        assert get_run_count("nonexistent") == 0


class TestEnvStorage:
    def test_write_read_env(self, isolated_config):
        write_instance_env("testvm", {"NAME": "testvm", "MGMT_IP": "10.0.0.1"})
        env = read_instance_env("testvm")
        assert env["NAME"] == "testvm"
        assert env["MGMT_IP"] == "10.0.0.1"

    def test_read_missing_env(self, isolated_config):
        assert read_instance_env("nonexistent") == {}

    def test_env_quoting(self, isolated_config):
        write_instance_env("testvm", {"PASSWORD": "secret with spaces"})
        env = read_instance_env("testvm")
        assert env["PASSWORD"] == "secret with spaces"

    def test_env_skips_none(self, isolated_config):
        write_instance_env("testvm", {"A": "1", "B": None})
        env = read_instance_env("testvm")
        assert "A" in env
        assert "B" not in env
