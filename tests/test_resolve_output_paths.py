import os
import tempfile
from argparse import Namespace

import pytest

from plain2code_arguments import (
    DEFAULT_BUILD_DEST,
    DEFAULT_BUILD_FOLDER,
    DEFAULT_CONFORMANCE_TESTS_DEST,
    DEFAULT_CONFORMANCE_TESTS_FOLDER,
    resolve_output_paths,
)


def _make_args(plain_file, **kwargs):
    """Build a minimal Namespace mimicking parsed args."""
    defaults = dict(
        filename=plain_file,
        build_folder=DEFAULT_BUILD_FOLDER,
        conformance_tests_folder=DEFAULT_CONFORMANCE_TESTS_FOLDER,
        build_dest=DEFAULT_BUILD_DEST,
        conformance_tests_dest=DEFAULT_CONFORMANCE_TESTS_DEST,
        base_folder=None,
    )
    defaults.update(kwargs)
    return Namespace(**defaults)


def test_default_folders_resolve_to_plain_file_dir():
    with tempfile.TemporaryDirectory() as plain_dir:
        plain_file = os.path.join(plain_dir, "module.plain")
        args = resolve_output_paths(_make_args(plain_file))

        assert args.build_folder == os.path.join(plain_dir, DEFAULT_BUILD_FOLDER)
        assert args.conformance_tests_folder == os.path.join(plain_dir, DEFAULT_CONFORMANCE_TESTS_FOLDER)
        assert args.build_dest == os.path.join(plain_dir, DEFAULT_BUILD_DEST)
        assert args.conformance_tests_dest == os.path.join(plain_dir, DEFAULT_CONFORMANCE_TESTS_DEST)


def test_relative_path_from_config_resolves_to_plain_file_dir():
    """A relative build_folder set in config (e.g. '../build') resolves relative to the plain file."""
    with tempfile.TemporaryDirectory() as plain_dir:
        plain_file = os.path.join(plain_dir, "module.plain")
        args = resolve_output_paths(_make_args(plain_file, build_folder="../build"))

        assert args.build_folder == os.path.normpath(os.path.join(plain_dir, "../build"))
        assert os.path.isabs(args.build_folder)


def test_absolute_path_left_unchanged():
    with tempfile.TemporaryDirectory() as plain_dir:
        plain_file = os.path.join(plain_dir, "module.plain")
        abs_path = "/some/absolute/path"
        args = resolve_output_paths(_make_args(plain_file, build_folder=abs_path))

        assert args.build_folder == abs_path


def test_base_folder_none_stays_none():
    with tempfile.TemporaryDirectory() as plain_dir:
        plain_file = os.path.join(plain_dir, "module.plain")
        args = resolve_output_paths(_make_args(plain_file, base_folder=None))

        assert args.base_folder is None


def test_base_folder_relative_resolves_to_plain_file_dir():
    with tempfile.TemporaryDirectory() as plain_dir:
        plain_file = os.path.join(plain_dir, "module.plain")
        args = resolve_output_paths(_make_args(plain_file, base_folder="base"))

        assert args.build_folder == os.path.join(plain_dir, DEFAULT_BUILD_FOLDER)
        assert args.base_folder == os.path.join(plain_dir, "base")


def test_plain_file_in_subdirectory():
    """Plain file in a subdirectory — all defaults land next to it, not at CWD."""
    with tempfile.TemporaryDirectory() as root:
        sub = os.path.join(root, "b", "c")
        os.makedirs(sub)
        plain_file = os.path.join(sub, "example.plain")
        args = resolve_output_paths(_make_args(plain_file))

        assert args.build_folder == os.path.join(sub, DEFAULT_BUILD_FOLDER)
