"""Tests for --logging-config-path resolution.

Same frame-of-reference rule as other path arguments: CLI values resolve
against CWD, config.yaml values resolve against the config file's
directory, and the default resolves against the spec file's directory.
"""

import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from plain2code_arguments import parse_arguments


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


def test_default_logging_config_path_resolves_next_to_spec(layout):
    with patch("os.getcwd", return_value=str(layout["cwd"])):
        args = parse_arguments([layout["plain_file"]])
    assert args.logging_config_path == str(layout["spec"] / "logging_config.yaml")


def test_cli_logging_config_path_resolves_against_cwd(layout):
    with patch("os.getcwd", return_value=str(layout["cwd"])):
        args = parse_arguments([layout["plain_file"], "--logging-config-path", "custom_logging.yaml"])
    assert args.logging_config_path == str(layout["cwd"] / "custom_logging.yaml")


def test_config_logging_config_path_resolves_against_config_dir(layout):
    (layout["config"] / "config.yaml").write_text("logging-config-path: logging_from_config.yaml\n")
    with patch("os.getcwd", return_value=str(layout["config"])):
        args = parse_arguments([layout["plain_file"]])
    assert args.logging_config_path == str(layout["config"] / "logging_from_config.yaml")


def test_cli_absolute_logging_config_path_preserved(layout):
    abs_path = str(layout["spec"] / "abs_logging.yaml")
    with patch("os.getcwd", return_value=str(layout["cwd"])):
        args = parse_arguments([layout["plain_file"], "--logging-config-path", abs_path])
    assert args.logging_config_path == abs_path


def test_cli_overrides_config_with_cwd_anchor(layout):
    (layout["config"] / "config.yaml").write_text("logging-config-path: from_config.yaml\n")
    with patch("os.getcwd", return_value=str(layout["cwd"])):
        args = parse_arguments([layout["plain_file"], "--logging-config-path", "from_cli.yaml"])
    assert args.logging_config_path == str(layout["cwd"] / "from_cli.yaml")
