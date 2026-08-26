"""Tests for the per-module output layout produced by ``PrepareRepositories``
and the tests-folder paths derived by ``ConformanceTests``.

Each module renders into a single tree under the build folder:

    <build>/<module>/.codeplain/   metadata, outside the git repos
    <build>/<module>/code/         git repo with the implementation code
    <build>/<module>/tests/        git repo with the conformance tests
"""

import json
import os
import shutil
import tempfile
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from conformance_fix_journal import CONFORMANCE_TEST_JOURNAL_SUBFOLDER
from git_utils import FUNCTIONAL_REQUIREMENT_FINISHED_COMMIT_MESSAGE, add_all_files_and_commit, init_git_repo
from memory_management import CONFORMANCE_TEST_MEMORY_SUBFOLDER
from partial_rendering import archive_missing_conformance_tests, get_plain_module_render_state, get_render_choices
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


def _init_repo_with_finished_frid(repo_path: str, module_name: str, frid: str = "1") -> None:
    os.makedirs(repo_path, exist_ok=True)
    init_git_repo(repo_path, module_name=module_name)
    (Path(repo_path) / f"frid_{frid}.txt").write_text(f"frid {frid}\n")
    add_all_files_and_commit(
        repo_path,
        FUNCTIONAL_REQUIREMENT_FINISHED_COMMIT_MESSAGE.format(frid),
        module_name=module_name,
        frid=frid,
    )


def _archive_module(module: PlainModule) -> None:
    """Zip the module's folder contents (code/, tests/ incl. .git) into <module>.module, then
    remove the folder, so the module exists only as an archive."""
    with zipfile.ZipFile(module.module_archive_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, _dirs, files in os.walk(module.module_folder):
            for name in files:
                full = os.path.join(root, name)
                zf.write(full, os.path.relpath(full, module.module_folder))
    shutil.rmtree(module.module_folder)


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


def test_fresh_render_inherits_memories_from_the_previous_module(root_module):
    """The previous module's distilled memories are copied into the new module's memory store
    (replacing any stale store), while its fix attempt journals stay behind."""
    previous = root_module.required_modules[-1]
    _init_repo_with_finished_frid(previous.module_build_folder, previous.module_name)
    memory_path = Path(previous.module_memory_folder) / CONFORMANCE_TEST_MEMORY_SUBFOLDER
    memory_path.mkdir(parents=True)
    (memory_path / "learning.json").write_text('{"memory_id": "learning"}')
    journal_path = Path(previous.module_memory_folder) / CONFORMANCE_TEST_JOURNAL_SUBFOLDER
    journal_path.mkdir(parents=True)
    (journal_path / "pr_middle__1.jsonl").write_text('{"attempt": 1}\n')

    stale_memory = Path(root_module.module_memory_folder) / CONFORMANCE_TEST_MEMORY_SUBFOLDER / "stale.json"
    stale_memory.parent.mkdir(parents=True)
    stale_memory.write_text('{"memory_id": "stale"}')

    render_context = _make_render_context(root_module, render_conformance_tests=False)
    PrepareRepositories().execute(render_context, None)

    inherited_memory_folder = Path(root_module.module_memory_folder) / CONFORMANCE_TEST_MEMORY_SUBFOLDER
    assert sorted(os.listdir(inherited_memory_folder)) == ["learning.json"]
    assert not stale_memory.exists()
    assert not (Path(root_module.module_memory_folder) / CONFORMANCE_TEST_JOURNAL_SUBFOLDER).exists()


def test_fresh_render_with_a_memoryless_previous_module_starts_with_an_empty_store(root_module):
    previous = root_module.required_modules[-1]
    _init_repo_with_finished_frid(previous.module_build_folder, previous.module_name)

    render_context = _make_render_context(root_module, render_conformance_tests=False)
    PrepareRepositories().execute(render_context, None)

    assert os.path.isdir(os.path.join(root_module.module_build_folder, ".git"))
    assert not os.path.exists(root_module.module_memory_folder)


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


# --------------------------------------------------------------------------
# Zipped modules — consuming a required module shipped as "<module>.module"
# --------------------------------------------------------------------------


@pytest.fixture
def root_module(get_test_data_path, tmp_build_folder):
    """pr_root -> pr_middle -> pr_leaf; root.required_modules[-1] == pr_middle."""
    return PlainModule("pr_root.plain", tmp_build_folder, [get_test_data_path("data/partial_rendering")])


def test_full_render_clones_from_archived_required_module(root_module):
    """A required module shipped only as a "<module>.module" archive is materialized and used as
    the clone starting point, and its code hash is recorded correctly in the module metadata."""
    previous = root_module.required_modules[-1]
    _init_repo_with_finished_frid(previous.module_build_folder, previous.module_name)
    _init_repo_with_finished_frid(previous.module_conformance_tests_folder, previous.module_name)
    _archive_module(previous)
    assert previous.is_archived_only()

    render_context = _make_render_context(root_module, render_conformance_tests=False)
    PrepareRepositories().execute(render_context, None)

    # The root code repo was cloned (from the materialized archive), and the required module was
    # materialized rather than left as an unusable archive.
    assert os.path.isdir(os.path.join(root_module.module_build_folder, ".git"))
    assert previous._resolved_module_folder is not None
    assert not os.path.exists(previous._default_module_folder)  # archive not unpacked in place
    assert os.path.isfile(previous.module_archive_path)  # archive preserved

    # seed_module_metadata recorded the required module's real code hash (ordering guard).
    metadata = root_module.load_module_metadata()
    assert metadata["required_modules_code_hash"] == previous.get_module_code_hash()

    previous.cleanup_scratch()


def test_archive_missing_conformance_tests_detects_code_only_required_module(root_module):
    """A required module shipped as a code-only .module (no tests/) is flagged only when conformance
    testing is enabled."""
    previous = root_module.required_modules[-1]
    _init_repo_with_finished_frid(previous.module_build_folder, previous.module_name)
    _archive_module(previous)  # code/ only, no tests/

    assert archive_missing_conformance_tests(root_module, True) is previous
    assert archive_missing_conformance_tests(root_module, False) is None


def test_archive_with_tests_not_flagged(root_module):
    previous = root_module.required_modules[-1]
    _init_repo_with_finished_frid(previous.module_build_folder, previous.module_name)
    _init_repo_with_finished_frid(previous.module_conformance_tests_folder, previous.module_name)
    _archive_module(previous)  # code/ + tests/

    assert archive_missing_conformance_tests(root_module, True) is None


def test_render_state_flags_missing_tests_and_offers_rerender(root_module):
    """A code-only archive (conformance on) surfaces a 'missing_conformance_tests' render state with
    a rerender choice and a quit choice."""
    previous = root_module.required_modules[-1]
    _init_repo_with_finished_frid(previous.module_build_folder, previous.module_name)
    _archive_module(previous)  # code/ only

    state = get_plain_module_render_state(root_module, render_conformance_tests=True)
    assert state is not None
    assert state.change_type == "missing_conformance_tests"
    assert state.change.module_name == previous.module_name

    choice_types = {c.choice_type for c in get_render_choices(root_module, state).values()}
    assert "quit" in choice_types
    assert "rerender_affected" in choice_types


def test_conformance_tests_resolver_overrides_default(tmp_build_folder):
    """A resolver maps an archived required module's name to its resolved (scratch) tests folder;
    unknown names fall back to the default base-folder path."""
    resolved = {"req": "/scratch/req/tests"}
    conformance_tests = ConformanceTests(
        tmp_build_folder,
        CONFORMANCE_TESTS_DEFINITION_FILE_NAME,
        resolve_module_tests_folder=lambda name: resolved.get(name),
    )
    assert conformance_tests.get_module_conformance_tests_folder("req") == "/scratch/req/tests"
    assert conformance_tests.get_module_conformance_tests_folder("other") == os.path.join(
        tmp_build_folder, "other", "tests"
    )


def test_conformance_tests_json_stored_relative_read_absolute(tmp_build_folder):
    """folder_name is stored relative to the module's tests folder on disk, but presented as an
    absolute path in memory — and the input dict is not mutated."""
    conformance_tests = ConformanceTests(tmp_build_folder, CONFORMANCE_TESTS_DEFINITION_FILE_NAME)
    tests_folder = conformance_tests.get_module_conformance_tests_folder("m")
    os.makedirs(tests_folder)

    absolute_folder = os.path.join(tests_folder, "1_feature")
    json_in = {"1": {"folder_name": absolute_folder, "functional_requirement": "do a thing"}}
    conformance_tests.dump_conformance_tests_json("m", json_in)

    # On disk: relative.
    with open(os.path.join(tests_folder, CONFORMANCE_TESTS_DEFINITION_FILE_NAME)) as f:
        raw = json.load(f)
    assert raw["1"]["folder_name"] == "1_feature"
    # Input dict not mutated.
    assert json_in["1"]["folder_name"] == absolute_folder

    # On read: absolute again.
    loaded = conformance_tests.get_conformance_tests_json("m")
    assert loaded["1"]["folder_name"] == absolute_folder
    assert loaded["1"]["functional_requirement"] == "do a thing"


def test_conformance_tests_json_resolves_against_scratch_base(tmp_build_folder):
    """A conformance_tests.json shipped in an archive (relative folder_name) resolves against the
    module's scratch tests folder when the module is materialized."""
    scratch_tests = os.path.join(tmp_build_folder, "scratch_extract", "tests")
    os.makedirs(scratch_tests)
    with open(os.path.join(scratch_tests, CONFORMANCE_TESTS_DEFINITION_FILE_NAME), "w") as f:
        json.dump({"1": {"folder_name": "1_feature", "functional_requirement": "x"}}, f)

    conformance_tests = ConformanceTests(
        tmp_build_folder,
        CONFORMANCE_TESTS_DEFINITION_FILE_NAME,
        resolve_module_tests_folder=lambda name: scratch_tests if name == "m" else None,
    )
    loaded = conformance_tests.get_conformance_tests_json("m")
    assert loaded["1"]["folder_name"] == os.path.join(scratch_tests, "1_feature")
