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

from change_detection import determine_partial_render_start
from git_utils import FUNCTIONAL_REQUIREMENT_FINISHED_COMMIT_MESSAGE, add_all_files_and_commit, init_git_repo
from plain2code_exceptions import ModuleDoesNotExistError
from plain_modules import MODULE_METADATA_FILENAME, PlainModule

# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


@pytest.fixture
def fixtures_dir(get_test_data_path):
    return get_test_data_path("data/partial_rendering")


@pytest.fixture
def tmp_build_folder():
    """Yield a temp build folder holding the per-module trees."""
    with tempfile.TemporaryDirectory() as build:
        yield build


@pytest.fixture
def solo_module(fixtures_dir, tmp_build_folder):
    return PlainModule("pr_solo.plain", tmp_build_folder, [fixtures_dir])


@pytest.fixture
def root_module(fixtures_dir, tmp_build_folder):
    """Builds pr_root -> pr_middle -> pr_leaf, each with 2 FRIDs."""
    return PlainModule("pr_root.plain", tmp_build_folder, [fixtures_dir])


@pytest.fixture
def code_var_module(fixtures_dir, tmp_build_folder):
    """A solo module whose FRID 2 pulls in a template with a code variable, so its
    raw markdown keeps a ``{{ variable_name }}`` placeholder that differs from the
    rendered (variable-substituted) text."""
    return PlainModule("pr_code_var.plain", tmp_build_folder, [fixtures_dir])


def _write_metadata(module: PlainModule, metadata: dict) -> None:
    folder = module.get_codeplain_folder()
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


# --------------------------------------------------------------------------
# update_frid_in_module_metadata — code-variable baseline consistency
# --------------------------------------------------------------------------


def test_update_frid_stores_raw_markdown_not_rendered_text(code_var_module):
    """The per-FRID metadata write must store the raw FR markdown (placeholder
    intact), identical to what the change-detection diff reads. Storing the
    rendered text (code variable substituted) would make every later diff report
    a spurious edit for that FRID."""
    raw = code_var_module._get_module_functional_requirements()
    # Sanity: FRID 2 really does carry an unsubstituted placeholder.
    assert "{{ variable_name }}" in raw[1]

    code_var_module.update_frid_in_module_metadata("1")
    code_var_module.update_frid_in_module_metadata("2")

    metadata = code_var_module.load_module_metadata()
    assert metadata["functionalities"] == raw
    # The substituted value must NOT leak into the stored baseline.
    assert "configurable-value" not in metadata["functionalities"][1]


def test_code_variable_frid_not_flagged_as_change(code_var_module):
    """After a (possibly interrupted) render persisted the per-FRID baseline,
    re-running with an unchanged spec must detect no change — even though the
    FR uses a code variable whose rendered text differs from its markdown."""
    # Seed the matching non-functional hash, as prepare_repositories does at the
    # start of a real render; update_frid then layers the functionalities on top.
    _write_metadata(
        code_var_module,
        {"non_functional_source_hash": code_var_module.get_module_non_functional_source_hash()},
    )
    code_var_module.update_frid_in_module_metadata("1")
    code_var_module.update_frid_in_module_metadata("2")

    assert determine_partial_render_start(code_var_module) is None


# --------------------------------------------------------------------------
# module folder layout
# --------------------------------------------------------------------------


def test_module_folder_layout(solo_module, tmp_build_folder):
    module_folder = os.path.join(tmp_build_folder, "pr_solo")
    assert solo_module.module_folder == module_folder
    assert solo_module.module_build_folder == os.path.join(module_folder, "code")
    assert solo_module.module_conformance_tests_folder == os.path.join(module_folder, "tests")
    assert solo_module.get_codeplain_folder() == os.path.join(module_folder, ".codeplain")
    assert solo_module.module_memory_folder == os.path.join(module_folder, ".memory")


def test_wipe_module_removes_whole_module_folder(solo_module):
    os.makedirs(solo_module.module_build_folder)
    os.makedirs(solo_module.module_conformance_tests_folder)
    solo_module.wipe_module()
    assert not os.path.exists(solo_module.module_folder)


# --------------------------------------------------------------------------
# seed_module_metadata
# --------------------------------------------------------------------------


def test_seed_module_metadata_writes_hashes(solo_module):
    solo_module.seed_module_metadata()
    assert solo_module.load_module_metadata() == solo_module.get_hashes()


def test_seed_module_metadata_overwrites_stale_functionalities(solo_module):
    _write_metadata(solo_module, {"source_hash": "stale", "functionalities": ["old fr"]})
    solo_module.seed_module_metadata()
    metadata = solo_module.load_module_metadata()
    assert metadata == solo_module.get_hashes()
    assert "functionalities" not in metadata


# --------------------------------------------------------------------------
# truncate_metadata_functionalities
# --------------------------------------------------------------------------


def test_truncate_metadata_functionalities_no_metadata_is_noop(solo_module):
    solo_module.truncate_metadata_functionalities("1")
    assert solo_module.load_module_metadata() is None


def test_truncate_metadata_functionalities_shortens_list_and_keeps_hashes(solo_module):
    _write_metadata(solo_module, {"source_hash": "abc", "functionalities": ["fr1", "fr2", "fr3"]})
    solo_module.truncate_metadata_functionalities("1")
    metadata = solo_module.load_module_metadata()
    assert metadata["functionalities"] == ["fr1"]
    assert metadata["source_hash"] == "abc"


def test_truncate_metadata_functionalities_noop_when_list_short_enough(solo_module):
    _write_metadata(solo_module, {"functionalities": ["fr1"]})
    solo_module.truncate_metadata_functionalities("2")
    assert solo_module.load_module_metadata()["functionalities"] == ["fr1"]


def test_truncate_metadata_functionalities_none_frid_empties_list(solo_module):
    _write_metadata(solo_module, {"functionalities": ["fr1", "fr2"]})
    solo_module.truncate_metadata_functionalities(None)
    assert solo_module.load_module_metadata()["functionalities"] == []


# --------------------------------------------------------------------------
# revert_code_to_frid
# --------------------------------------------------------------------------


def _commit_finished_frid(module: PlainModule, frid: str) -> None:
    marker = Path(module.module_build_folder) / f"frid_{frid}.txt"
    marker.write_text(f"frid {frid}\n")
    add_all_files_and_commit(
        module.module_build_folder,
        FUNCTIONAL_REQUIREMENT_FINISHED_COMMIT_MESSAGE.format(frid),
        module_name=module.module_name,
        frid=frid,
    )


def test_revert_code_to_frid_reverts_repo_and_trims_metadata(solo_module):
    os.makedirs(solo_module.module_build_folder)
    init_git_repo(solo_module.module_build_folder, module_name=solo_module.module_name)
    _commit_finished_frid(solo_module, "1")
    _commit_finished_frid(solo_module, "2")
    _write_metadata(solo_module, {"source_hash": "abc", "functionalities": ["fr1", "fr2"]})

    solo_module.revert_code_to_frid("1")

    assert os.path.exists(os.path.join(solo_module.module_build_folder, "frid_1.txt"))
    assert not os.path.exists(os.path.join(solo_module.module_build_folder, "frid_2.txt"))
    metadata = solo_module.load_module_metadata()
    assert metadata["functionalities"] == ["fr1"]
    assert metadata["source_hash"] == "abc"
    # The metadata folder lives outside the code repo and must survive the revert.
    assert os.path.exists(solo_module.get_codeplain_folder())


def test_revert_code_to_frid_none_reverts_to_initial_state(solo_module):
    os.makedirs(solo_module.module_build_folder)
    init_git_repo(solo_module.module_build_folder, module_name=solo_module.module_name)
    _commit_finished_frid(solo_module, "1")
    _write_metadata(solo_module, {"functionalities": ["fr1"]})

    solo_module.revert_code_to_frid(None)

    assert not os.path.exists(os.path.join(solo_module.module_build_folder, "frid_1.txt"))
    assert solo_module.load_module_metadata()["functionalities"] == []
