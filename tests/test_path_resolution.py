import os

import pytest

from path_resolution import resolve_path


CWD = "/work/cwd"
CONFIG_DIR = "/work/project/config"
SPEC_DIR = "/work/project/spec"


def test_cli_relative_resolves_against_cwd():
    result = resolve_path("out", "cli", cwd=CWD, config_dir=CONFIG_DIR, spec_dir=SPEC_DIR)
    assert result == os.path.normpath("/work/cwd/out")


def test_config_relative_resolves_against_config_dir():
    result = resolve_path("out", "config", cwd=CWD, config_dir=CONFIG_DIR, spec_dir=SPEC_DIR)
    assert result == os.path.normpath("/work/project/config/out")


def test_default_relative_resolves_against_spec_dir():
    result = resolve_path("out", "default", cwd=CWD, config_dir=CONFIG_DIR, spec_dir=SPEC_DIR)
    assert result == os.path.normpath("/work/project/spec/out")


def test_absolute_path_is_returned_unchanged_for_all_sources():
    for source in ("cli", "config", "default"):
        result = resolve_path("/abs/path", source, cwd=CWD, config_dir=CONFIG_DIR, spec_dir=SPEC_DIR)
        assert result == os.path.normpath("/abs/path")


def test_dotdot_segments_are_normalized():
    result = resolve_path("../sibling", "cli", cwd=CWD, config_dir=CONFIG_DIR, spec_dir=SPEC_DIR)
    assert result == os.path.normpath("/work/sibling")


def test_leading_tilde_is_expanded(monkeypatch):
    monkeypatch.setenv("HOME", "/home/alice")
    result = resolve_path("~/scripts/run.sh", "cli", cwd=CWD, config_dir=CONFIG_DIR, spec_dir=SPEC_DIR)
    assert result == os.path.normpath("/home/alice/scripts/run.sh")


def test_tilde_expansion_applies_regardless_of_source(monkeypatch):
    monkeypatch.setenv("HOME", "/home/alice")
    for source in ("cli", "config", "default"):
        result = resolve_path("~/x", source, cwd=CWD, config_dir=CONFIG_DIR, spec_dir=SPEC_DIR)
        assert result == os.path.normpath("/home/alice/x")


def test_config_source_requires_config_dir():
    with pytest.raises(ValueError, match="config_dir"):
        resolve_path("out", "config", cwd=CWD, config_dir=None, spec_dir=SPEC_DIR)


def test_config_dir_may_be_none_for_cli_and_default():
    cli_result = resolve_path("out", "cli", cwd=CWD, config_dir=None, spec_dir=SPEC_DIR)
    default_result = resolve_path("out", "default", cwd=CWD, config_dir=None, spec_dir=SPEC_DIR)
    assert cli_result == os.path.normpath("/work/cwd/out")
    assert default_result == os.path.normpath("/work/project/spec/out")


def test_unknown_source_raises():
    with pytest.raises(ValueError, match="Unknown path source"):
        resolve_path("out", "bogus", cwd=CWD, config_dir=CONFIG_DIR, spec_dir=SPEC_DIR)  # type: ignore[arg-type]
