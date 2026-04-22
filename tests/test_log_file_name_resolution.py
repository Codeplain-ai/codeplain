"""Tests for --log-file-name resolution.

Same frame-of-reference rule as other path arguments: CLI values resolve
against CWD, config.yaml values resolve against the config file's
directory, and the default resolves against the spec file's directory.

Also verifies the "--log-file-name cannot be used when --log-to-file is
False" validation now uses the recorded argument source rather than a
string-equality heuristic against the default.
"""

import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from plain2code_arguments import DEFAULT_LOG_FILE_NAME, parse_arguments


@pytest.fixture
def layout():
    """Three separate directories: spec dir, config dir, and CWD."""
    with tempfile.TemporaryDirectory() as root:
        spec_dir = Path(root) / "spec_dir"
        config_dir = Path(root) / "config_dir"
        cwd = Path(root) / "cwd"
        spec_dir.mkdir()
        config_dir.mkdir()
        cwd.mkdir()
        (spec_dir / "module.plain").write_text("")
        yield {
            "spec": spec_dir,
            "config": config_dir,
            "cwd": cwd,
            "plain_file": str(spec_dir / "module.plain"),
        }


def test_default_log_file_name_resolves_next_to_spec(layout):
    with patch("os.getcwd", return_value=str(layout["cwd"])):
        args = parse_arguments([layout["plain_file"]])
    assert args.log_file_name == str(layout["spec"] / DEFAULT_LOG_FILE_NAME)


def test_cli_log_file_name_resolves_against_cwd(layout):
    with patch("os.getcwd", return_value=str(layout["cwd"])):
        args = parse_arguments([layout["plain_file"], "--log-file-name", "my.log"])
    assert args.log_file_name == str(layout["cwd"] / "my.log")


def test_config_log_file_name_resolves_against_config_dir(layout):
    (layout["config"] / "config.yaml").write_text("log-file-name: from_config.log\n")
    with patch("os.getcwd", return_value=str(layout["config"])):
        args = parse_arguments([layout["plain_file"]])
    assert args.log_file_name == str(layout["config"] / "from_config.log")


def test_cli_absolute_log_file_name_preserved(layout):
    abs_path = str(layout["spec"] / "abs.log")
    with patch("os.getcwd", return_value=str(layout["cwd"])):
        args = parse_arguments([layout["plain_file"], "--log-file-name", abs_path])
    assert args.log_file_name == abs_path


def test_log_file_name_with_no_log_to_file_errors_when_cli_supplied(layout, capsys):
    with patch("os.getcwd", return_value=str(layout["cwd"])):
        with pytest.raises(SystemExit):
            parse_arguments(
                [layout["plain_file"], "--log-file-name", "my.log", "--no-log-to-file"]
            )
    err = capsys.readouterr().err
    assert "--log-file-name cannot be used when --log-to-file is False" in err


def test_log_file_name_with_no_log_to_file_errors_when_config_supplied(layout, capsys):
    """Config-supplied --log-file-name also counts as explicit."""
    (layout["config"] / "config.yaml").write_text("log-file-name: from_config.log\n")
    with patch("os.getcwd", return_value=str(layout["config"])):
        with pytest.raises(SystemExit):
            parse_arguments([layout["plain_file"], "--no-log-to-file"])
    err = capsys.readouterr().err
    assert "--log-file-name cannot be used when --log-to-file is False" in err


def test_no_log_to_file_with_default_log_file_name_is_allowed(layout):
    """When no explicit --log-file-name is given, --no-log-to-file is accepted."""
    with patch("os.getcwd", return_value=str(layout["cwd"])):
        args = parse_arguments([layout["plain_file"], "--no-log-to-file"])
    assert args.log_to_file is False
