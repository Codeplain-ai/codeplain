"""Tests for carrying project lessons along the render chain.

The lessons that accumulate in a multi-module project are the ones that are not about any module in it -
parent POMs, dependency scopes, logging binders, framework idioms. They are carried by copying the file from
one module to the next rather than by sharing one file, so that each module's copy is a snapshot of what the
chain knew when that module was rendered and a partial re-render cannot read what a later module learned.
"""

import json
import os
from unittest.mock import MagicMock

import pytest

from memory_management import CONFORMANCE_TEST_LESSONS_FILE_NAME, PROJECT_LESSONS_FILE_NAME, MemoryManager
from module_renderer import ModuleRenderer

POM_LESSON = {"lesson": "Conformance test projects must declare spring-boot-starter-parent.", "scope": "project"}
BINDER_LESSON = {"lesson": "The SLF4J binder must be logback-classic.", "scope": "project"}


def _write_lessons(folder, file_name, lessons):
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, file_name), "w", encoding="utf-8") as handle:
        json.dump({"lessons": lessons}, handle)


def _read_lessons(folder, file_name):
    with open(os.path.join(folder, file_name), encoding="utf-8") as handle:
        return json.load(handle)["lessons"]


# --- what reaches a prompt --------------------------------------------------------------------------------


def test_both_lesson_sets_are_offered_to_the_prompt(tmp_path):
    _write_lessons(str(tmp_path), PROJECT_LESSONS_FILE_NAME, [POM_LESSON])
    _write_lessons(str(tmp_path), CONFORMANCE_TEST_LESSONS_FILE_NAME, [{"lesson": "a module rule"}])

    memory_files, content = MemoryManager.fetch_memory_files(str(tmp_path))

    assert set(memory_files) == {PROJECT_LESSONS_FILE_NAME, CONFORMANCE_TEST_LESSONS_FILE_NAME}
    assert "spring-boot-starter-parent" in content[PROJECT_LESSONS_FILE_NAME]


def test_only_the_lesson_files_reach_the_prompt(tmp_path):
    """The folder also holds the fix journal and the boilerplate profile; neither may be sent."""
    _write_lessons(str(tmp_path), PROJECT_LESSONS_FILE_NAME, [POM_LESSON])
    with open(os.path.join(str(tmp_path), "failure_profile.json"), "w", encoding="utf-8") as profile:
        profile.write("{}")
    os.makedirs(os.path.join(str(tmp_path), "fix_journal"), exist_ok=True)
    with open(os.path.join(str(tmp_path), "fix_journal", "module--1.json"), "w", encoding="utf-8") as journal:
        journal.write("{}")

    memory_files, _ = MemoryManager.fetch_memory_files(str(tmp_path))

    assert memory_files == [PROJECT_LESSONS_FILE_NAME]


def test_a_module_with_no_lessons_yet_sends_nothing(tmp_path):
    assert MemoryManager.fetch_memory_files(str(tmp_path)) == ([], {})


# --- carrying the file forward ----------------------------------------------------------------------------


def test_a_module_inherits_the_project_lessons_of_the_one_before_it(tmp_path):
    predecessor, successor = str(tmp_path / "a"), str(tmp_path / "b")
    _write_lessons(predecessor, PROJECT_LESSONS_FILE_NAME, [POM_LESSON])

    MemoryManager.inherit_project_lessons(predecessor, successor)

    assert _read_lessons(successor, PROJECT_LESSONS_FILE_NAME) == [POM_LESSON]


def test_inheriting_replaces_what_was_there_so_the_file_is_a_snapshot_of_the_chain(tmp_path):
    predecessor, successor = str(tmp_path / "a"), str(tmp_path / "b")
    _write_lessons(predecessor, PROJECT_LESSONS_FILE_NAME, [POM_LESSON])
    _write_lessons(successor, PROJECT_LESSONS_FILE_NAME, [BINDER_LESSON])

    MemoryManager.inherit_project_lessons(predecessor, successor)

    assert _read_lessons(successor, PROJECT_LESSONS_FILE_NAME) == [POM_LESSON]


def test_the_module_own_lessons_are_not_inherited(tmp_path):
    predecessor, successor = str(tmp_path / "a"), str(tmp_path / "b")
    _write_lessons(predecessor, CONFORMANCE_TEST_LESSONS_FILE_NAME, [{"lesson": "about HttpClient"}])

    MemoryManager.inherit_project_lessons(predecessor, successor)

    assert not os.path.exists(os.path.join(successor, CONFORMANCE_TEST_LESSONS_FILE_NAME))


def test_a_predecessor_that_learned_nothing_leaves_what_is_already_here_alone(tmp_path):
    """The first module in a chain keeps accumulating across renders; nothing wipes it."""
    predecessor, successor = str(tmp_path / "a"), str(tmp_path / "b")
    os.makedirs(predecessor, exist_ok=True)
    _write_lessons(successor, PROJECT_LESSONS_FILE_NAME, [BINDER_LESSON])

    MemoryManager.inherit_project_lessons(predecessor, successor)

    assert _read_lessons(successor, PROJECT_LESSONS_FILE_NAME) == [BINDER_LESSON]


def test_the_first_module_in_the_chain_has_nothing_to_inherit(tmp_path):
    successor = str(tmp_path / "b")

    MemoryManager.inherit_project_lessons(None, successor)

    assert not os.path.exists(os.path.join(successor, PROJECT_LESSONS_FILE_NAME))


# --- who counts as the module before this one -------------------------------------------------------------


def _module(name, required=()):
    module = MagicMock()
    module.module_name = name
    module.required_modules = list(required)
    module.module_memory_folder = f"/build/{name}/.memory"
    module.all_required_modules = []
    return module


def _renderer(top):
    renderer = ModuleRenderer.__new__(ModuleRenderer)
    renderer.plain_module = top
    return renderer


@pytest.fixture
def chain():
    """utils <- http_client <- top, in the order a depth first render visits them."""
    utils = _module("d365_utils")
    http_client = _module("d365_http_client", required=[utils])
    http_client.all_required_modules = [utils]
    top = _module("d365", required=[http_client])
    top.all_required_modules = [utils, http_client]
    return utils, http_client, top


def test_the_chain_is_ordered_the_way_the_render_visits_it(chain):
    utils, http_client, top = chain

    ordered = _renderer(top)._chain_order()

    assert [module.module_name for module in ordered] == ["d365_utils", "d365_http_client", "d365"]


def test_each_module_inherits_from_the_one_rendered_immediately_before_it(chain):
    utils, http_client, top = chain
    renderer = _renderer(top)

    assert renderer._predecessor_memory_folder(utils) is None
    assert renderer._predecessor_memory_folder(http_client) == utils.module_memory_folder
    assert renderer._predecessor_memory_folder(top) == http_client.module_memory_folder


def test_a_module_appearing_twice_in_the_tree_is_only_visited_once(chain):
    """Two modules requiring the same dependency must not give it two places in the chain."""
    utils, http_client, top = chain
    top.all_required_modules = [utils, utils, http_client]

    ordered = _renderer(top)._chain_order()

    assert [module.module_name for module in ordered] == ["d365_utils", "d365_http_client", "d365"]


def test_a_module_outside_this_render_has_no_predecessor(chain):
    _, _, top = chain

    assert _renderer(top)._predecessor_memory_folder(_module("unrelated")) is None
