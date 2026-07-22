"""Tests for the per-module output layout produced by ``PrepareRepositories``
and the tests-folder paths derived by ``ConformanceTests``.

Each module renders into a single tree under the build folder:

    <build>/<module>/.codeplain/   metadata, outside the git repos
    <build>/<module>/code/         git repo with the implementation code
    <build>/<module>/tests/        git repo with the conformance tests
"""

import os
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from plain_modules import PlainModule
from render_machine.actions.prepare_repositories import PrepareRepositories
from render_machine.conformance_tests import CONFORMANCE_TESTS_DEFINITION_FILE_NAME, ConformanceTests

# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


@pytest.fixture
def tmp_build_folder():
    with tempfile.TemporaryDirectory() as build:
        yield build


@pytest.fixture
def solo_module(get_test_data_path, tmp_build_folder):
    return PlainModule("pr_solo.plain", tmp_build_folder, [get_test_data_path("data/partial_rendering")])


def _make_render_context(module: PlainModule, render_conformance_tests: bool) -> SimpleNamespace:
    return SimpleNamespace(
        render_range=None,
        plain_module=module,
        required_modules=module.required_modules,
        build_folder=module.module_build_folder,
        module_name=module.module_name,
        run_state=SimpleNamespace(render_id="test-render-id"),
        render_conformance_tests=render_conformance_tests,
        conformance_tests=ConformanceTests(module.build_folder, CONFORMANCE_TESTS_DEFINITION_FILE_NAME),
        base_folder=None,
    )


# --------------------------------------------------------------------------
# PrepareRepositories — fresh render
# --------------------------------------------------------------------------


def test_fresh_render_creates_code_and_tests_repos_and_seeds_metadata(solo_module):
    render_context = _make_render_context(solo_module, render_conformance_tests=True)

    PrepareRepositories().execute(render_context, None)

    assert os.path.isdir(os.path.join(solo_module.module_build_folder, ".git"))
    assert os.path.isdir(os.path.join(solo_module.module_conformance_tests_folder, ".git"))
    assert solo_module.load_module_metadata() == solo_module.get_hashes()


def test_fresh_render_wipes_the_module_folder_first(solo_module):
    stale_file = Path(solo_module.module_folder) / "stale.txt"
    stale_memory = Path(solo_module.module_memory_folder) / "stale_memory.md"
    stale_memory.parent.mkdir(parents=True)
    stale_memory.write_text("stale")
    stale_file.write_text("stale")

    render_context = _make_render_context(solo_module, render_conformance_tests=True)
    PrepareRepositories().execute(render_context, None)

    assert not stale_file.exists()
    assert not stale_memory.exists()


def test_fresh_render_without_conformance_tests_does_not_create_tests_folder(solo_module):
    render_context = _make_render_context(solo_module, render_conformance_tests=False)

    PrepareRepositories().execute(render_context, None)

    assert os.path.isdir(os.path.join(solo_module.module_build_folder, ".git"))
    assert not os.path.exists(solo_module.module_conformance_tests_folder)


# --------------------------------------------------------------------------
# ConformanceTests — tests-folder paths
# --------------------------------------------------------------------------


def test_module_conformance_tests_folder_is_tests_subfolder(tmp_build_folder):
    conformance_tests = ConformanceTests(tmp_build_folder, CONFORMANCE_TESTS_DEFINITION_FILE_NAME)
    assert conformance_tests.get_module_conformance_tests_folder("some_module") == os.path.join(
        tmp_build_folder, "some_module", "tests"
    )


def test_cross_module_copy_lands_in_hidden_folder_under_tests(tmp_build_folder):
    """When a module regression-tests a required module, the copied conformance
    tests land in <build>/<module>/tests/.<required_module>/<subfolder>."""
    conformance_tests = ConformanceTests(tmp_build_folder, CONFORMANCE_TESTS_DEFINITION_FILE_NAME)
    original_folder = os.path.join(
        conformance_tests.get_module_conformance_tests_folder("required_module"), "1_frid_feature"
    )

    source_folder, new_folder = conformance_tests.get_source_conformance_test_folder_name(
        "top_module",
        [],
        "required_module",
        original_folder,
    )

    expected = os.path.join(tmp_build_folder, "top_module", "tests", ".required_module", "1_frid_feature")
    assert new_folder == expected
    assert source_folder == expected
