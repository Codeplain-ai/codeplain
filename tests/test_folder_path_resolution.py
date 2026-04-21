"""Tests for folder-path argument resolution.

Covers --base-folder, --build-folder, --conformance-tests-folder,
--build-dest, --conformance-tests-dest, --template-dir.

The rule: CLI values resolve against CWD, config values resolve against
the config file's directory, and values left at their default (for the
output folders) resolve against the spec file's directory.
"""

import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from plain2code_arguments import (
    DEFAULT_BUILD_DEST,
    DEFAULT_BUILD_FOLDER,
    DEFAULT_CONFORMANCE_TESTS_DEST,
    DEFAULT_CONFORMANCE_TESTS_FOLDER,
    parse_arguments,
)


@pytest.fixture
def layout():
    """Three separate directories: one for the spec, one for the config, one as CWD."""
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


# ---- Default-source resolution (spec-dir-anchored) ------------------------


def test_missing_build_folder_defaults_next_to_spec(layout):
    with patch("os.getcwd", return_value=str(layout["cwd"])):
        args = parse_arguments([layout["plain_file"]])
    assert args.build_folder == str(layout["spec"] / DEFAULT_BUILD_FOLDER)


def test_missing_conformance_tests_folder_defaults_next_to_spec(layout):
    with patch("os.getcwd", return_value=str(layout["cwd"])):
        args = parse_arguments([layout["plain_file"]])
    assert args.conformance_tests_folder == str(layout["spec"] / DEFAULT_CONFORMANCE_TESTS_FOLDER)


def test_missing_build_dest_defaults_next_to_spec(layout):
    with patch("os.getcwd", return_value=str(layout["cwd"])):
        args = parse_arguments([layout["plain_file"]])
    assert args.build_dest == str(layout["spec"] / DEFAULT_BUILD_DEST)


def test_missing_conformance_tests_dest_defaults_next_to_spec(layout):
    with patch("os.getcwd", return_value=str(layout["cwd"])):
        args = parse_arguments([layout["plain_file"]])
    assert args.conformance_tests_dest == str(layout["spec"] / DEFAULT_CONFORMANCE_TESTS_DEST)


def test_optional_folders_remain_none_when_unset(layout):
    with patch("os.getcwd", return_value=str(layout["cwd"])):
        args = parse_arguments([layout["plain_file"]])
    assert args.base_folder is None
    assert args.template_dir is None


# ---- CLI-source resolution (CWD-anchored) ---------------------------------


def test_cli_build_folder_resolves_against_cwd(layout):
    with patch("os.getcwd", return_value=str(layout["cwd"])):
        args = parse_arguments([layout["plain_file"], "--build-folder", "out"])
    assert args.build_folder == str(layout["cwd"] / "out")


def test_cli_base_folder_resolves_against_cwd(layout):
    base = layout["cwd"] / "base"
    base.mkdir()
    with patch("os.getcwd", return_value=str(layout["cwd"])):
        args = parse_arguments([layout["plain_file"], "--base-folder", "base"])
    assert args.base_folder == str(base)


def test_cli_template_dir_resolves_against_cwd(layout):
    tmpl = layout["cwd"] / "templates"
    tmpl.mkdir()
    with patch("os.getcwd", return_value=str(layout["cwd"])):
        args = parse_arguments([layout["plain_file"], "--template-dir", "templates"])
    assert args.template_dir == str(tmpl)


def test_cli_dotdot_folder_resolves_against_cwd(layout):
    with patch("os.getcwd", return_value=str(layout["cwd"])):
        args = parse_arguments(
            [layout["plain_file"], "--build-folder", "../spec_dir/custom_out"]
        )
    assert args.build_folder == str(layout["spec"] / "custom_out")


def test_cli_absolute_path_preserved(layout):
    abs_path = str(layout["spec"] / "explicit_out")
    with patch("os.getcwd", return_value=str(layout["cwd"])):
        args = parse_arguments([layout["plain_file"], "--build-folder", abs_path])
    assert args.build_folder == abs_path


# ---- Config-source resolution (config-dir-anchored) -----------------------


def test_config_build_folder_resolves_against_config_dir(layout):
    (layout["config"] / "config.yaml").write_text("build-folder: out_from_config\n")
    with patch("os.getcwd", return_value=str(layout["config"])):
        args = parse_arguments([layout["plain_file"]])
    assert args.build_folder == str(layout["config"] / "out_from_config")


def test_config_all_output_folders_resolve_against_config_dir(layout):
    (layout["config"] / "config.yaml").write_text(
        "build-folder: b\n"
        "conformance-tests-folder: ct\n"
        "build-dest: d\n"
        "conformance-tests-dest: cd\n"
    )
    with patch("os.getcwd", return_value=str(layout["config"])):
        args = parse_arguments([layout["plain_file"]])
    assert args.build_folder == str(layout["config"] / "b")
    assert args.conformance_tests_folder == str(layout["config"] / "ct")
    assert args.build_dest == str(layout["config"] / "d")
    assert args.conformance_tests_dest == str(layout["config"] / "cd")


def test_config_template_dir_resolves_against_config_dir(layout):
    (layout["config"] / "config.yaml").write_text("template-dir: my_templates\n")
    with patch("os.getcwd", return_value=str(layout["config"])):
        args = parse_arguments([layout["plain_file"]])
    assert args.template_dir == str(layout["config"] / "my_templates")


def test_cli_overrides_config_and_uses_cwd_anchor(layout):
    """A CLI flag that overrides a config value must use CLI semantics (CWD)."""
    (layout["config"] / "config.yaml").write_text("build-folder: from_config\n")
    with patch("os.getcwd", return_value=str(layout["cwd"])):
        args = parse_arguments([layout["plain_file"], "--build-folder", "from_cli"])
    assert args.build_folder == str(layout["cwd"] / "from_cli")


# ---- Equality checks happen on resolved paths ----------------------------


def test_build_folder_and_build_dest_equality_detected_after_resolution(layout, capsys):
    """Two different relative paths that resolve to the same absolute path must trip the check."""
    (layout["config"] / "config.yaml").write_text(
        "build-folder: same\n"
        "build-dest: same\n"
    )
    with patch("os.getcwd", return_value=str(layout["config"])):
        with pytest.raises(SystemExit):
            parse_arguments([layout["plain_file"]])
    err = capsys.readouterr().err
    assert "--build-folder and --build-dest cannot be the same" in err
