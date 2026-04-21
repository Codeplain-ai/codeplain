"""Tests for argument-source provenance tracking in plain2code_arguments."""

import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from plain2code_arguments import ARGUMENT_SOURCES, parse_arguments


@pytest.fixture
def project():
    """Temporary directory with a plain file. CWD is patched to the same directory."""
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "module.plain").write_text("")
        with patch("os.getcwd", return_value=d):
            yield d


def _sources(args):
    return getattr(args, ARGUMENT_SOURCES)


def test_default_when_neither_cli_nor_config(project):
    args = parse_arguments([os.path.join(project, "module.plain")])
    assert _sources(args)["build_folder"] == "default"
    assert _sources(args)["conformance_tests_folder"] == "default"


def test_cli_value_is_marked_as_cli(project):
    args = parse_arguments([os.path.join(project, "module.plain"), "--build-folder", "out"])
    assert _sources(args)["build_folder"] == "cli"
    assert args.build_folder == os.path.join(project, "out")


def test_cli_value_equal_to_default_is_still_cli(project):
    """Passing the literal default on the CLI is still an explicit CLI choice."""
    args = parse_arguments([os.path.join(project, "module.plain"), "--build-folder", "plain_modules"])
    assert _sources(args)["build_folder"] == "cli"


def test_config_value_is_marked_as_config(project):
    (Path(project) / "config.yaml").write_text("build-folder: from_config\n")
    args = parse_arguments([os.path.join(project, "module.plain")])
    assert _sources(args)["build_folder"] == "config"
    assert args.build_folder == os.path.join(project, "from_config")


def test_cli_wins_over_config(project):
    (Path(project) / "config.yaml").write_text("build-folder: from_config\n")
    args = parse_arguments([os.path.join(project, "module.plain"), "--build-folder", "from_cli"])
    assert _sources(args)["build_folder"] == "cli"
    assert args.build_folder == os.path.join(project, "from_cli")


def test_boolean_flag_provenance(project):
    args = parse_arguments([os.path.join(project, "module.plain"), "--verbose"])
    assert _sources(args)["verbose"] == "cli"
    assert args.verbose is True


def test_boolean_flag_default(project):
    args = parse_arguments([os.path.join(project, "module.plain")])
    assert _sources(args)["verbose"] == "default"
    assert args.verbose is False


def test_boolean_flag_from_config(project):
    (Path(project) / "config.yaml").write_text("verbose: true\n")
    args = parse_arguments([os.path.join(project, "module.plain")])
    assert _sources(args)["verbose"] == "config"
    assert args.verbose is True


def test_mixed_sources(project):
    """CLI, config, and default values coexist on the same invocation."""
    (Path(project) / "config.yaml").write_text("build-folder: from_config\n")
    args = parse_arguments(
        [os.path.join(project, "module.plain"), "--conformance-tests-folder", "ct_from_cli"]
    )
    srcs = _sources(args)
    assert srcs["conformance_tests_folder"] == "cli"
    assert srcs["build_folder"] == "config"
    assert srcs["build_dest"] == "default"
