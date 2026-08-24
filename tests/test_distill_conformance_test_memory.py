"""Tests for the DistillConformanceTestMemory postprocessing action."""

import json
import os
import tempfile
from types import SimpleNamespace

import pytest

from memory_management import CONFORMANCE_TEST_MEMORY_SUBFOLDER, MemoryManager
from render_machine.actions.distill_conformance_test_memory import DistillConformanceTestMemory


class FakeCodeplainAPI:
    def __init__(self, response_files=None, error=None):
        self.response_files = response_files or {}
        self.error = error
        self.calls = []

    def distill_conformance_test_memory(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.response_files


@pytest.fixture
def build_folder():
    with tempfile.TemporaryDirectory() as folder:
        yield folder


@pytest.fixture
def memory_folder():
    with tempfile.TemporaryDirectory() as folder:
        yield folder


def make_render_context(api, memory_folder, build_folder):
    memory_manager = MemoryManager(api, memory_folder)
    return SimpleNamespace(
        memory_manager=memory_manager,
        codeplain_api=api,
        build_folder=build_folder,
        plain_source_tree={},
        module_name="mod",
        frid_context=SimpleNamespace(frid="1", linked_resources={}),
        get_required_modules_functionalities=lambda: {},
        run_state=SimpleNamespace(render_id="test-render-id"),
    )


def seed_journal(memory_manager, module="mod", frid="1"):
    journal = memory_manager.journal
    journal.open_attempt(module, frid, 1, "tests failed")
    journal.record_fix(
        module,
        frid,
        {"hypothesis": "root cause", "approach": "the fix"},
        ["src/app.py"],
        "IMPLEMENTATION_CODE",
        {"src/app.py": "diff"},
        "the issue as the fixer saw it",
    )
    journal.record_result(module, frid, passed=True)


def test_no_journals_means_no_api_call(memory_folder, build_folder):
    api = FakeCodeplainAPI()
    render_context = make_render_context(api, memory_folder, build_folder)

    outcome, payload = DistillConformanceTestMemory().execute(render_context, None)

    assert outcome == DistillConformanceTestMemory.SUCCESSFUL_OUTCOME
    assert payload is None
    assert api.calls == []


def test_journals_are_distilled_stored_and_cleared(memory_folder, build_folder):
    memory_content = json.dumps({"memory_id": "learning-1", "key_learnings": "start the server first"})
    api = FakeCodeplainAPI(response_files={"learning-1.json": memory_content})
    render_context = make_render_context(api, memory_folder, build_folder)
    seed_journal(render_context.memory_manager)

    outcome, _ = DistillConformanceTestMemory().execute(render_context, None)

    assert outcome == DistillConformanceTestMemory.SUCCESSFUL_OUTCOME
    [call] = api.calls
    assert call["frid"] == "1"
    assert call["module_name"] == "mod"
    assert call["fix_journals"][0]["module"] == "mod"
    assert call["fix_journals"][0]["attempts"][0]["hypothesis"] == "root cause"

    memory_file = os.path.join(memory_folder, CONFORMANCE_TEST_MEMORY_SUBFOLDER, "learning-1.json")
    assert os.path.isfile(memory_file)
    assert render_context.memory_manager.journal.collect_all() == []


def test_a_delete_response_removes_an_existing_memory(memory_folder, build_folder):
    memory_path = os.path.join(memory_folder, CONFORMANCE_TEST_MEMORY_SUBFOLDER)
    os.makedirs(memory_path)
    with open(os.path.join(memory_path, "stale.json"), "w") as memory_file:
        memory_file.write(json.dumps({"memory_id": "stale"}))

    api = FakeCodeplainAPI(response_files={"stale.json": None})
    render_context = make_render_context(api, memory_folder, build_folder)
    seed_journal(render_context.memory_manager)

    DistillConformanceTestMemory().execute(render_context, None)

    assert not os.path.exists(os.path.join(memory_path, "stale.json"))


def test_wipe_removes_memories_and_journals(build_folder):
    memory_folder = os.path.join(build_folder, ".memory")
    memory_manager = MemoryManager(FakeCodeplainAPI(), memory_folder)
    seed_journal(memory_manager)
    memory_manager.store_memory_files({"learning.json": json.dumps({"memory_id": "learning"})})

    memory_manager.wipe()

    assert not os.path.exists(memory_folder)
    assert memory_manager.journal.collect_all() == []
    assert MemoryManager.fetch_memory_files(memory_folder) == ([], {})


def test_wipe_of_a_missing_store_is_a_no_op(build_folder):
    missing_folder = os.path.join(build_folder, "does_not_exist", ".memory")
    MemoryManager(FakeCodeplainAPI(), missing_folder).wipe()

    assert not os.path.exists(missing_folder)


def test_an_api_failure_keeps_the_journals_and_does_not_raise(memory_folder, build_folder):
    api = FakeCodeplainAPI(error=RuntimeError("api down"))
    render_context = make_render_context(api, memory_folder, build_folder)
    seed_journal(render_context.memory_manager)

    outcome, _ = DistillConformanceTestMemory().execute(render_context, None)

    assert outcome == DistillConformanceTestMemory.SUCCESSFUL_OUTCOME
    assert len(render_context.memory_manager.journal.collect_all()) == 1
