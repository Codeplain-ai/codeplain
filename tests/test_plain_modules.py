"""Tests for ``PlainModule`` — the change-detection and navigation helpers
that power the partial-rendering feature.

These tests build real ``PlainModule`` instances from fixtures in
``tests/data/partial_rendering`` and use a per-test temporary build folder.
"""

import json
import os
import tempfile
from pathlib import Path

import pytest

from git_utils import FUNCTIONAL_REQUIREMENT_FINISHED_COMMIT_MESSAGE, add_all_files_and_commit, init_git_repo
from plain2code_exceptions import ModuleDoesNotExistError
from plain_modules import CODEPLAIN_METADATA_FOLDER, MODULE_METADATA_FILENAME, PlainModule

# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


@pytest.fixture
def fixtures_dir(get_test_data_path):
    return get_test_data_path("data/partial_rendering")


@pytest.fixture
def tmp_build_folders():
    """Yield (build_folder, conformance_tests_folder) as temp dirs."""
    with tempfile.TemporaryDirectory() as build, tempfile.TemporaryDirectory() as conformance:
        yield build, conformance


@pytest.fixture
def solo_module(fixtures_dir, tmp_build_folders):
    build, conformance = tmp_build_folders
    return PlainModule("pr_solo.plain", build, conformance, [fixtures_dir])


@pytest.fixture
def root_module(fixtures_dir, tmp_build_folders):
    """Builds pr_root -> pr_middle -> pr_leaf, each with 2 FRIDs."""
    build, conformance = tmp_build_folders
    return PlainModule("pr_root.plain", build, conformance, [fixtures_dir])


def _write_metadata(module: PlainModule, metadata: dict) -> None:
    folder = os.path.join(module.module_build_folder, CODEPLAIN_METADATA_FOLDER)
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, MODULE_METADATA_FILENAME), "w", encoding="utf-8") as f:
        json.dump(metadata, f)


def _init_build_repo_with_finished_frid(module: PlainModule, frid: str) -> None:
    """Initialise a git repo in ``module.module_build_folder`` and commit a
    ``FUNCTIONAL_REQUIREMENT_FINISHED`` checkpoint for the given FRID."""
    os.makedirs(module.module_build_folder, exist_ok=True)
    init_git_repo(module.module_build_folder, module_name=module.module_name)

    # Add a file so the commit is non-empty.
    marker = Path(module.module_build_folder) / f"frid_{frid}.txt"
    marker.write_text(f"frid {frid}\n")
    add_all_files_and_commit(
        module.module_build_folder,
        FUNCTIONAL_REQUIREMENT_FINISHED_COMMIT_MESSAGE.format(frid),
        module_name=module.module_name,
        frid=frid,
    )


# --------------------------------------------------------------------------
# all_required_modules
# --------------------------------------------------------------------------


def test_all_required_modules_empty_for_leaf(solo_module):
    assert solo_module.all_required_modules == []


def test_all_required_modules_flattens_tree(root_module):
    names = [m.module_name for m in root_module.all_required_modules]
    # Tree is: root -> middle -> leaf; flattened depth-first, leaf comes before middle.
    assert names == ["pr_leaf", "pr_middle"]


# --------------------------------------------------------------------------
# has_plain_spec_changed
# --------------------------------------------------------------------------


def test_has_plain_spec_changed_true_when_no_metadata(solo_module):
    """With no metadata on disk, there's no recorded hash to compare — the
    module is treated as changed so the renderer re-runs it."""
    assert solo_module.has_plain_spec_changed() is True


def test_has_plain_spec_changed_true_when_source_hash_field_missing(solo_module):
    _write_metadata(solo_module, {"functionalities": []})
    assert solo_module.has_plain_spec_changed() is True


def test_has_plain_spec_changed_false_when_hash_matches(solo_module):
    _write_metadata(solo_module, {"source_hash": solo_module.get_module_source_hash()})
    assert solo_module.has_plain_spec_changed() is False


def test_has_plain_spec_changed_true_when_hash_differs(solo_module):
    _write_metadata(solo_module, {"source_hash": "definitely-not-the-current-hash"})
    assert solo_module.has_plain_spec_changed() is True


# --------------------------------------------------------------------------
# has_required_modules_code_changed
# --------------------------------------------------------------------------


def test_has_required_modules_code_changed_false_when_no_required_modules(solo_module):
    # No required modules → nothing to track, so never "changed".
    assert solo_module.has_required_modules_code_changed() is False


def test_has_required_modules_code_changed_true_when_no_metadata(root_module):
    assert root_module.has_required_modules_code_changed() is True


def test_has_required_modules_code_changed_true_when_hash_field_missing(root_module):
    _write_metadata(root_module, {"source_hash": root_module.get_module_source_hash()})
    assert root_module.has_required_modules_code_changed() is True


def test_has_required_modules_code_changed_false_when_hash_matches(root_module):
    previous_module = root_module.required_modules[-1]
    _write_metadata(
        root_module,
        {
            "source_hash": root_module.get_module_source_hash(),
            "required_modules_code_hash": previous_module.get_module_code_hash(),
        },
    )
    assert root_module.has_required_modules_code_changed() is False


def test_has_required_modules_code_changed_true_when_hash_differs(root_module):
    _write_metadata(
        root_module,
        {
            "source_hash": root_module.get_module_source_hash(),
            "required_modules_code_hash": "stale-code-hash",
        },
    )
    assert root_module.has_required_modules_code_changed() is True


# --------------------------------------------------------------------------
# get_required_module_by_name
# --------------------------------------------------------------------------


def test_get_required_module_by_name_finds_required_module(root_module):
    leaf = root_module.get_required_module_by_name("pr_leaf")
    assert leaf.module_name == "pr_leaf"


def test_get_required_module_by_name_raises_for_unknown(root_module):
    with pytest.raises(ModuleDoesNotExistError, match="phantom"):
        root_module.get_required_module_by_name("phantom")


def test_get_required_module_by_name_raises_for_self(root_module):
    """Unlike the previous ``get_module_by_name``, the required-only lookup
    does not match the top module itself."""
    with pytest.raises(ModuleDoesNotExistError, match="pr_root"):
        root_module.get_required_module_by_name("pr_root")


# --------------------------------------------------------------------------
# get_next_module
# --------------------------------------------------------------------------


def test_get_next_module_returns_next_in_sequence(root_module):
    # all_required_modules = [pr_leaf, pr_middle]
    nxt = root_module.get_next_module("pr_leaf")
    assert nxt.module_name == "pr_middle"


def test_get_next_module_returns_top_module_when_at_last_required_module(root_module):
    """When the given module is the last required module, ``get_next_module``
    returns the top-level module — callers then progress to the root's first FRID."""
    nxt = root_module.get_next_module("pr_middle")
    assert nxt is root_module


def test_get_next_module_returns_none_when_asked_for_next_after_top_module(root_module):
    """There is no module after the top-level module — ``get_next_module``
    signals that by returning ``None`` (callers fall back explicitly)."""
    assert root_module.get_next_module("pr_root") is None


def test_get_next_module_raises_when_module_not_found(root_module):
    """Unknown module names raise ``ModuleDoesNotExistError``."""
    with pytest.raises(ModuleDoesNotExistError, match="unknown"):
        root_module.get_next_module("unknown")


# --------------------------------------------------------------------------
# get_next_frid
# --------------------------------------------------------------------------


def test_get_next_frid_within_same_module(root_module):
    # Each fixture module has FRIDs ["1", "2"].
    next_frid, next_module = root_module.get_next_frid("1", "pr_leaf")
    assert next_frid == "2"
    assert next_module.module_name == "pr_leaf"


def test_get_next_frid_crosses_module_boundary(root_module):
    # After pr_leaf's last FRID, progress to pr_middle's first.
    next_frid, next_module = root_module.get_next_frid("2", "pr_leaf")
    assert next_module.module_name == "pr_middle"
    assert next_frid == "1"


def test_get_next_frid_from_last_required_module_progresses_to_root(root_module):
    # After pr_middle's last FRID, progress to the root's first FRID.
    next_frid, next_module = root_module.get_next_frid("2", "pr_middle")
    assert next_module is root_module
    assert next_frid == "1"


# --------------------------------------------------------------------------
# get_module_render_status
# --------------------------------------------------------------------------


def test_get_module_render_status_no_rendering(root_module):
    assert root_module.get_module_render_status() == (None, None)


def test_get_module_render_status_returns_from_leaf_when_only_leaf_rendered(root_module):
    leaf = root_module.get_required_module_by_name("pr_leaf")
    _init_build_repo_with_finished_frid(leaf, "1")

    module_name, frid = root_module.get_module_render_status()
    assert module_name == "pr_leaf"
    assert frid == "1"


def test_get_module_render_status_prefers_most_progressed_module(root_module):
    """The scan walks required_modules in reverse order — the right-most
    rendered module wins."""
    leaf = root_module.get_required_module_by_name("pr_leaf")
    middle = root_module.get_required_module_by_name("pr_middle")
    _init_build_repo_with_finished_frid(leaf, "2")
    _init_build_repo_with_finished_frid(middle, "1")

    module_name, frid = root_module.get_module_render_status()
    assert module_name == "pr_middle"
    assert frid == "1"


def test_get_module_render_status_returns_root_when_root_has_checkpoint(root_module):
    """A checkpoint in the root's own build folder takes precedence over
    required-module checkpoints."""
    leaf = root_module.get_required_module_by_name("pr_leaf")
    _init_build_repo_with_finished_frid(leaf, "1")
    _init_build_repo_with_finished_frid(root_module, "2")

    module_name, frid = root_module.get_module_render_status()
    assert module_name == "pr_root"
    assert frid == "2"


# --------------------------------------------------------------------------
# is_module_fully_rendered
# --------------------------------------------------------------------------


def test_is_module_fully_rendered_false_when_nothing_rendered(solo_module):
    assert solo_module.is_module_fully_rendered() is False


def test_is_module_fully_rendered_false_when_only_first_frid_rendered(solo_module):
    _init_build_repo_with_finished_frid(solo_module, "1")
    assert solo_module.is_module_fully_rendered() is False


def test_is_module_fully_rendered_true_when_last_frid_rendered(solo_module):
    # solo module has FRIDs ["1", "2", "3"]; "3" is the last.
    _init_build_repo_with_finished_frid(solo_module, "3")
    assert solo_module.is_module_fully_rendered() is True
