"""Behaviour of the individual environment check handlers."""

import os
import socket
import sys

import pytest

from env_check.checks import (
    check_command_available,
    check_directory_writable,
    check_env_var_set,
    check_file_exists,
    check_python_module_importable,
    check_tcp_port_free,
    check_tcp_service_reachable,
    parse_version,
    version_at_least,
)
from env_check.types import STATUS_FAILED, STATUS_PASSED


class TestVersions:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("Python 3.11.9", (3, 11, 9)),
            ('openjdk version "17.0.9" 2023-10-17', (17, 0, 9)),
            ("v20.11.0", (20, 11, 0)),
            ("git version 2.39", (2, 39)),
            ("no numbers here", None),
        ],
    )
    def test_parse_version(self, text, expected):
        assert parse_version(text) == expected

    @pytest.mark.parametrize(
        "found,minimum,expected",
        [
            ((3, 11, 9), (3, 11), True),
            ((3, 10), (3, 11), False),
            ((3,), (3, 0, 0), True),
            ((17, 0, 9), (17,), True),
        ],
    )
    def test_version_at_least(self, found, minimum, expected):
        assert version_at_least(found, minimum) is expected


class TestCommandAvailable:
    def test_missing_command_fails(self):
        status, detail = check_command_available({"command": "definitely-not-a-real-binary-xyz"})

        assert status == STATUS_FAILED
        assert "not found on PATH" in detail

    def test_present_command_passes(self):
        status, detail = check_command_available({"command": "python3", "version_arg": "--version"})

        assert status == STATUS_PASSED
        assert "found at" in detail

    def test_version_below_minimum_fails(self):
        status, detail = check_command_available(
            {"command": "python3", "version_arg": "--version", "min_version": "99.0"}
        )

        assert status == STATUS_FAILED
        assert "older than" in detail

    def test_unreadable_version_does_not_fail_the_check(self):
        # A toolchain whose version string cannot be parsed must not block a render.
        status, _ = check_command_available(
            {
                "command": "python3",
                "version_arg": "--version",
                "version_regex": r"(THIS-WILL-NOT-MATCH)",
                "min_version": "3.11",
            }
        )

        assert status == STATUS_PASSED


class TestEnvVarSet:
    def test_missing_variable_fails(self, monkeypatch):
        monkeypatch.delenv("PREFLIGHT_TEST_VAR", raising=False)

        status, detail = check_env_var_set({"name": "PREFLIGHT_TEST_VAR"})

        assert status == STATUS_FAILED
        assert "is not set" in detail

    def test_empty_variable_fails_by_default(self, monkeypatch):
        monkeypatch.setenv("PREFLIGHT_TEST_VAR", "  ")

        status, _ = check_env_var_set({"name": "PREFLIGHT_TEST_VAR"})

        assert status == STATUS_FAILED

    def test_empty_variable_passes_when_allowed(self, monkeypatch):
        monkeypatch.setenv("PREFLIGHT_TEST_VAR", "")

        status, _ = check_env_var_set({"name": "PREFLIGHT_TEST_VAR", "allow_empty": True})

        assert status == STATUS_PASSED

    def test_secret_value_is_never_reported(self, monkeypatch):
        monkeypatch.setenv("PREFLIGHT_TEST_VAR", "super-secret-token")

        status, detail = check_env_var_set({"name": "PREFLIGHT_TEST_VAR"})

        assert status == STATUS_PASSED
        assert "super-secret-token" not in detail

    def test_pattern_mismatch_does_not_leak_the_value(self, monkeypatch):
        monkeypatch.setenv("PREFLIGHT_TEST_VAR", "super-secret-token")

        status, detail = check_env_var_set({"name": "PREFLIGHT_TEST_VAR", "pattern": r"^sk-"})

        assert status == STATUS_FAILED
        assert "super-secret-token" not in detail


class TestFilesystem:
    def test_missing_file_fails(self, tmp_path):
        status, _ = check_file_exists({"path": str(tmp_path / "nope.sh")})

        assert status == STATUS_FAILED

    def test_existing_file_passes(self, tmp_path):
        script = tmp_path / "run.sh"
        script.write_text("#!/bin/bash\n")

        status, _ = check_file_exists({"path": str(script)})

        assert status == STATUS_PASSED

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
    def test_non_executable_file_fails_when_executability_is_required(self, tmp_path):
        script = tmp_path / "run.sh"
        script.write_text("#!/bin/bash\n")
        script.chmod(0o644)

        status, detail = check_file_exists({"path": str(script), "must_be_executable": True})

        assert status == STATUS_FAILED
        assert "not executable" in detail

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
    def test_executable_file_passes(self, tmp_path):
        script = tmp_path / "run.sh"
        script.write_text("#!/bin/bash\n")
        script.chmod(0o755)

        status, _ = check_file_exists({"path": str(script), "must_be_executable": True})

        assert status == STATUS_PASSED

    def test_directory_writable_uses_the_nearest_existing_ancestor(self, tmp_path):
        target = tmp_path / "does" / "not" / "exist" / "yet"

        status, detail = check_directory_writable({"path": str(target)})

        assert status == STATUS_PASSED
        assert str(tmp_path) in detail


class TestPythonModule:
    def test_importable_module_passes(self):
        status, _ = check_python_module_importable({"module": "json"})

        assert status == STATUS_PASSED

    def test_missing_module_fails(self):
        status, detail = check_python_module_importable({"module": "definitely_not_installed_xyz"})

        assert status == STATUS_FAILED
        assert "not importable" in detail

    def test_missing_interpreter_fails(self):
        status, detail = check_python_module_importable({"module": "json", "interpreter": "python-that-does-not-exist"})

        assert status == STATUS_FAILED
        assert "not found on PATH" in detail


class TestNetwork:
    def test_free_port_passes(self):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            free_port = probe.getsockname()[1]

        status, _ = check_tcp_port_free({"port": free_port})

        assert status == STATUS_PASSED

    def test_occupied_port_fails(self):
        with socket.socket() as taken:
            taken.bind(("127.0.0.1", 0))
            taken.listen(1)
            port = taken.getsockname()[1]

            status, detail = check_tcp_port_free({"port": port})

        assert status == STATUS_FAILED
        assert "already in use" in detail

    def test_unreachable_service_fails(self):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            closed_port = probe.getsockname()[1]

        status, _ = check_tcp_service_reachable({"host": "127.0.0.1", "port": closed_port})

        assert status == STATUS_FAILED

    def test_reachable_service_passes(self):
        with socket.socket() as server:
            server.bind(("127.0.0.1", 0))
            server.listen(1)
            port = server.getsockname()[1]

            status, _ = check_tcp_service_reachable({"host": "127.0.0.1", "port": port})

        assert status == STATUS_PASSED


def test_handlers_never_use_a_shell():
    """The registry must not hand user- or server-supplied text to a shell."""
    import inspect

    import env_check.checks as checks_module

    source = inspect.getsource(checks_module)
    assert "shell=True" not in source
    assert "os.system" not in source
    assert "os.popen" not in source


def test_environment_is_not_mutated_by_checks(monkeypatch):
    before = dict(os.environ)
    check_env_var_set({"name": "PATH"})
    assert dict(os.environ) == before
