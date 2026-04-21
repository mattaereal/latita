from __future__ import annotations

import pytest

from latita.utils import (
    validate_name,
    validate_ip,
    validate_cpus,
    validate_memory,
    validate_disk_size,
    shred_file,
)


class TestValidateName:
    def test_valid(self):
        assert validate_name("vm-001") == "vm-001"
        assert validate_name("my_vm") == "my_vm"

    def test_empty(self):
        with pytest.raises(Exception):
            validate_name("")

    def test_invalid_chars(self):
        with pytest.raises(Exception):
            validate_name("vm@001")


class TestValidateIp:
    def test_ipv4(self):
        assert validate_ip("10.0.0.1") == "10.0.0.1"

    def test_ipv6(self):
        assert validate_ip("::1") == "::1"

    def test_invalid(self):
        with pytest.raises(Exception):
            validate_ip("not-an-ip")


class TestValidateCpus:
    def test_valid(self):
        assert validate_cpus("4") == 4

    def test_too_low(self):
        with pytest.raises(Exception):
            validate_cpus("0")

    def test_too_high(self):
        with pytest.raises(Exception):
            validate_cpus("300")

    def test_not_a_number(self):
        with pytest.raises(Exception):
            validate_cpus("abc")


class TestValidateMemory:
    def test_valid(self):
        assert validate_memory("4096") == 4096

    def test_too_low(self):
        with pytest.raises(Exception):
            validate_memory("128")

    def test_not_a_number(self):
        with pytest.raises(Exception):
            validate_memory("abc")


class TestValidateDiskSize:
    def test_valid_gb(self):
        assert validate_disk_size("20G") == "20G"

    def test_valid_tb(self):
        assert validate_disk_size("1T") == "1T"

    def test_missing_suffix(self):
        with pytest.raises(Exception):
            validate_disk_size("20")

    def test_invalid_suffix(self):
        with pytest.raises(Exception):
            validate_disk_size("20X")


class TestShredFile:
    def test_shred_missing_noop(self, tmp_path):
        missing = tmp_path / "missing.txt"
        shred_file(missing)
        assert not missing.exists()

    def test_shred_existing(self, tmp_path):
        target = tmp_path / "secret.txt"
        target.write_text("sensitive data")
        shred_file(target)
        assert not target.exists()
