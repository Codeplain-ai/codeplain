import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from plain2code_arguments import resolve_config_file
from plain2code_exceptions import AmbiguousConfigFileError


@pytest.fixture
def two_dirs():
    """Provide two separate temporary directories: one for the plain file, one as CWD."""
    with tempfile.TemporaryDirectory() as plain_dir:
        with tempfile.TemporaryDirectory() as cwd:
            yield plain_dir, cwd


def _plain_file(plain_dir):
    return os.path.join(plain_dir, "module.plain")


def test_config_in_plain_file_dir_only(two_dirs):
    plain_dir, cwd = two_dirs
    config = Path(plain_dir) / "config.yaml"
    config.write_text("verbose: true\n")

    with patch("os.getcwd", return_value=cwd):
        result = resolve_config_file("config.yaml", _plain_file(plain_dir))

    assert result == os.path.normpath(str(config))


def test_config_in_cwd_only(two_dirs):
    plain_dir, cwd = two_dirs
    config = Path(cwd) / "config.yaml"
    config.write_text("verbose: true\n")

    with patch("os.getcwd", return_value=cwd):
        result = resolve_config_file("config.yaml", _plain_file(plain_dir))

    assert result == os.path.normpath(str(config))


def test_config_in_both_locations_raises(two_dirs):
    plain_dir, cwd = two_dirs
    (Path(plain_dir) / "config.yaml").write_text("verbose: true\n")
    (Path(cwd) / "config.yaml").write_text("verbose: false\n")

    with patch("os.getcwd", return_value=cwd):
        with pytest.raises(AmbiguousConfigFileError) as exc_info:
            resolve_config_file("config.yaml", _plain_file(plain_dir))

    assert plain_dir in str(exc_info.value)
    assert cwd in str(exc_info.value)


def test_config_not_found_returns_none(two_dirs):
    plain_dir, cwd = two_dirs

    with patch("os.getcwd", return_value=cwd):
        result = resolve_config_file("config.yaml", _plain_file(plain_dir))

    assert result is None


def test_config_same_dir_no_error():
    """When the plain file and CWD are in the same directory, a single config file is fine."""
    with tempfile.TemporaryDirectory() as d:
        config = Path(d) / "config.yaml"
        config.write_text("verbose: true\n")

        with patch("os.getcwd", return_value=d):
            result = resolve_config_file("config.yaml", os.path.join(d, "module.plain"))

    assert result == os.path.normpath(str(config))


def test_custom_config_name_found_in_plain_file_dir(two_dirs):
    """A custom --config-name is also looked up in the two locations."""
    plain_dir, cwd = two_dirs
    config = Path(plain_dir) / "myconfig.yaml"
    config.write_text("verbose: true\n")

    with patch("os.getcwd", return_value=cwd):
        result = resolve_config_file("myconfig.yaml", _plain_file(plain_dir))

    assert result == os.path.normpath(str(config))
