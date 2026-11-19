"""A regeneration starts from the last finished functionality.

An interrupted regeneration, or a failed attempt inside one render, leaves commits and changes on
top of the last "fully implemented" or "fully regenerated" commit. The next attempt must not start
from that code.
"""

import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from git import Repo

import git_utils
from plain2code_exceptions import InvalidGitRepositoryError
from render_machine import render_utils
from render_machine.actions.prepare_repositories import PrepareRepositories

MODULE = "ledger"
FRIDS = ["ledger-storage", "add-expense", "list-expenses"]


def _commit(repo_path, message, frid, files, module_name=MODULE):
    for name, text in files.items():
        path = Path(repo_path) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    git_utils.add_all_files_and_commit(repo_path, message.format(frid), module_name, frid, "render-id")


def _rendered_repo(repo_path):
    git_utils.init_git_repo(repo_path, MODULE, "render-id")
    for frid in FRIDS:
        _commit(repo_path, git_utils.FUNCTIONAL_REQUIREMENT_FINISHED_COMMIT_MESSAGE, frid, {f"{frid}.py": "v1\n"})


def _interrupted_regeneration(repo_path):
    """A finished regeneration of add-expense, then a second one that was interrupted."""
    _commit(
        repo_path,
        git_utils.FUNCTIONAL_REQUIREMENT_REGENERATED_COMMIT_MESSAGE,
        "add-expense",
        {"add-expense.py": "v2\n"},
    )
    finished = Repo(repo_path).head.commit.hexsha
    _commit(
        repo_path,
        git_utils.FUNCTIONAL_REQUIREMENT_IMPLEMENTED_COMMIT_MESSAGE,
        "add-expense",
        {"add-expense.py": "interrupted\n"},
    )
    (Path(repo_path) / "add-expense.py").write_text("interrupted, not committed\n")
    (Path(repo_path) / "new_file.py").write_text("not committed\n")
    return finished


def test_reset_goes_to_the_newest_finished_commit(tmp_path):
    _rendered_repo(tmp_path)
    finished = _interrupted_regeneration(tmp_path)

    git_utils.revert_to_last_finished_commit(tmp_path, MODULE)

    assert Repo(tmp_path).head.commit.hexsha == finished
    assert (tmp_path / "add-expense.py").read_text() == "v2\n"
    # The functionalities after the regenerated one keep their code.
    assert (tmp_path / "list-expenses.py").read_text() == "v1\n"
    assert not (tmp_path / "new_file.py").exists()


def test_reset_without_a_regeneration_goes_to_the_last_implemented_commit(tmp_path):
    _rendered_repo(tmp_path)
    finished = Repo(tmp_path).head.commit.hexsha
    _commit(
        tmp_path, git_utils.FUNCTIONAL_REQUIREMENT_IMPLEMENTED_COMMIT_MESSAGE, "add-expense", {"add-expense.py": "x\n"}
    )

    git_utils.revert_to_last_finished_commit(tmp_path, MODULE)

    assert Repo(tmp_path).head.commit.hexsha == finished


def test_reset_ignores_the_commits_of_another_module(tmp_path):
    git_utils.init_git_repo(tmp_path, "base", "render-id")
    _commit(tmp_path, git_utils.FUNCTIONAL_REQUIREMENT_FINISHED_COMMIT_MESSAGE, "base-fr", {"a.py": "a\n"}, "base")

    with pytest.raises(InvalidGitRepositoryError):
        git_utils.revert_to_last_finished_commit(tmp_path, MODULE)


def _render_context(code, tests, conformance_tests_json):
    store = {"json": conformance_tests_json}
    conformance_tests = SimpleNamespace(
        get_conformance_tests_json=lambda module_name: dict(store["json"]),
        dump_conformance_tests_json=lambda module_name, data: store.update(json=data),
        get_module_conformance_tests_folder=lambda module_name: str(tests),
    )
    metadata = {"functionalities": [{"id": frid, "markdown": f"- {frid}"} for frid in FRIDS]}
    return SimpleNamespace(
        is_regenerate=True,
        render_range=["add-expense"],
        build_folder=str(code),
        module_name=MODULE,
        plain_module=SimpleNamespace(load_module_metadata=lambda: metadata),
        render_conformance_tests=True,
        conformance_tests=conformance_tests,
        starting_frid=None,
        old_frid_spec=None,
        code_functional_requirements=None,
    )


def test_regeneration_after_an_interrupted_one_starts_from_finished_code_and_tests(tmp_path):
    code, tests = tmp_path / "code", tmp_path / "tests"
    _rendered_repo(code)
    finished_code = _interrupted_regeneration(code)
    _rendered_repo(tests)
    finished_tests = Repo(tests).head.commit.hexsha
    # The interrupted regeneration removed the old tests of add-expense and did not commit.
    os.remove(tests / "add-expense.py")
    render_context = _render_context(code, tests, {"add-expense": {"folder_name": str(tests / "tests_add_expense")}})

    outcome, _ = PrepareRepositories().execute(render_context, None)

    assert outcome == PrepareRepositories.SUCCESSFUL_OUTCOME
    assert Repo(code).head.commit.hexsha == finished_code
    assert (code / "add-expense.py").read_text() == "v2\n"
    assert not Repo(code).is_dirty(untracked_files=True)
    assert Repo(tests).head.commit.hexsha == finished_tests


def test_retry_inside_a_regeneration_removes_only_the_failed_attempt(tmp_path):
    _rendered_repo(tmp_path)
    finished = _interrupted_regeneration(tmp_path)
    render_context = SimpleNamespace(is_regenerate=True, build_folder=str(tmp_path), module_name=MODULE)

    render_utils.revert_changes_for_frid(render_context)

    assert Repo(tmp_path).head.commit.hexsha == finished
    assert (tmp_path / "list-expenses.py").read_text() == "v1\n"


def test_reset_ignores_a_module_whose_name_starts_with_the_module_name(tmp_path):
    git_utils.init_git_repo(tmp_path, "ledger_base", "render-id")
    _commit(
        tmp_path,
        git_utils.FUNCTIONAL_REQUIREMENT_FINISHED_COMMIT_MESSAGE,
        "storage",
        {"storage.py": "v1\n"},
        module_name="ledger_base",
    )

    with pytest.raises(InvalidGitRepositoryError):
        git_utils.revert_to_last_finished_commit(tmp_path, MODULE)


def test_regenerated_frids_ignore_a_module_whose_name_starts_with_the_module_name(tmp_path):
    _rendered_repo(tmp_path)
    _commit(
        tmp_path,
        git_utils.FUNCTIONAL_REQUIREMENT_REGENERATED_COMMIT_MESSAGE,
        "list-expenses",
        {"list-expenses.py": "v2\n"},
        module_name="ledger_base",
    )

    assert git_utils.get_frids_regenerated_after_frid(tmp_path, "add-expense", MODULE) == []
