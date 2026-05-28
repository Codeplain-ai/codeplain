"""Tests for script-path argument resolution.

Covers the rule that the resolution base for a relative script path is
determined by where the value was written -- CWD for CLI, config-file
directory for config.yaml values.
"""

import os
import stat
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from plain2code_arguments import parse_arguments


def _make_script(path: Path) -> None:
    path.write_text("#!/bin/sh\necho hello\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


@pytest.fixture
def layout():
    """Project layout with separate dirs for spec, config, and CWD, each with a script."""
    with tempfile.TemporaryDirectory() as root:
        spec_dir = Path(root) / "spec_dir"
        config_dir = Path(root) / "config_dir"
        cwd = Path(root) / "cwd"
        spec_dir.mkdir()
        config_dir.mkdir()
        cwd.mkdir()

        (spec_dir / "module.plain").write_text("")
        _make_script(spec_dir / "script_in_spec.sh")
        _make_script(config_dir / "script_in_config.sh")
        _make_script(cwd / "script_in_cwd.sh")

        yield {
            "spec": spec_dir,
            "config": config_dir,
            "cwd": cwd,
            "plain_file": str(spec_dir / "module.plain"),
        }


def test_cli_script_path_resolves_against_cwd(layout):
    with patch("os.getcwd", return_value=str(layout["cwd"])):
        args = parse_arguments([layout["plain_file"], "--unittests-script", "script_in_cwd.sh"])
    assert args.unittests_script == str(layout["cwd"] / "script_in_cwd.sh")


def test_config_script_path_resolves_against_config_dir(layout):
    (layout["config"] / "config.yaml").write_text("unittests-script: script_in_config.sh\n")

    with patch("os.getcwd", return_value=str(layout["config"])):
        args = parse_arguments([layout["plain_file"]])

    assert args.unittests_script == str(layout["config"] / "script_in_config.sh")


def test_cli_script_path_does_not_fall_back_to_config_dir(layout):
    """A CLI value that happens to match a file in the config dir must NOT resolve there."""
    (layout["config"] / "config.yaml").write_text("verbose: true\n")

    with patch("os.getcwd", return_value=str(layout["cwd"])):
        with pytest.raises(FileNotFoundError, match="unittests_script"):
            # script_in_config.sh does not exist relative to CWD, so resolution must fail
            parse_arguments([layout["plain_file"], "--unittests-script", "script_in_config.sh"])


def test_cli_script_with_dotdot_resolves_against_cwd(layout):
    """Tab-completion-style relative path like ../config_dir/script.sh works from CWD."""
    with patch("os.getcwd", return_value=str(layout["cwd"])):
        args = parse_arguments([layout["plain_file"], "--unittests-script", "../config_dir/script_in_config.sh"])
    assert args.unittests_script == str(layout["config"] / "script_in_config.sh")


def test_absolute_script_path_preserved(layout):
    abs_path = str(layout["spec"] / "script_in_spec.sh")
    with patch("os.getcwd", return_value=str(layout["cwd"])):
        args = parse_arguments([layout["plain_file"], "--unittests-script", abs_path])
    assert args.unittests_script == abs_path


def test_missing_script_raises(layout):
    with patch("os.getcwd", return_value=str(layout["cwd"])):
        with pytest.raises(FileNotFoundError):
            parse_arguments([layout["plain_file"], "--unittests-script", "nope.sh"])


def test_all_three_script_args_resolved(layout):
    """unittests-script, conformance-tests-script, prepare-environment-script all go through the same path."""
    (layout["config"] / "config.yaml").write_text(
        "unittests-script: script_in_config.sh\n"
        "conformance-tests-script: script_in_config.sh\n"
        "prepare-environment-script: script_in_config.sh\n"
    )

    with patch("os.getcwd", return_value=str(layout["config"])):
        args = parse_arguments([layout["plain_file"]])

    expected = str(layout["config"] / "script_in_config.sh")
    assert args.unittests_script == expected
    assert args.conformance_tests_script == expected
    assert args.prepare_environment_script == expected
