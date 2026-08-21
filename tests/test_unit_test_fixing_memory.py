"""Tests for what the unit test fixer is given.

Two things are new. It sees the memory, which it previously did not at all. And inside a conformance fix loop
it sees the implementation changes that loop just made, so that a unit test asserting the behaviour one of them
replaced is updated rather than answered by reverting the change - without which the two loops trade the same
lines back and forth, neither able to see the other's history.
"""

import json
import os
from types import SimpleNamespace

import pytest

from memory_management import GLOBAL_MEMORY_FILE_NAME, MemoryManager
from render_machine.actions.fix_unit_tests import FixUnitTests
from render_machine.render_types import UnitTestsRunningContext


class RecordingApi:
    def __init__(self):
        self.calls = []

    def fix_unittests_issue(self, *args, **kwargs):
        self.calls.append(args)
        return {}


@pytest.fixture
def render_context(tmp_path):
    build_folder = tmp_path / "code"
    build_folder.mkdir()
    (build_folder / "app.py").write_text("def login():\n    return 200\n")

    memory_folder = str(tmp_path / ".memory")
    os.makedirs(memory_folder)
    with open(os.path.join(memory_folder, GLOBAL_MEMORY_FILE_NAME), "w") as global_memory:
        json.dump({"learnings": [{"learning": "the build needs the logging binder"}]}, global_memory)

    plain_source_tree = {"functional specs": [{"content": "A :User: can log in."}]}

    return SimpleNamespace(
        codeplain_api=RecordingApi(),
        memory_manager=MemoryManager(None, memory_folder),
        build_folder=str(build_folder),
        plain_source_tree=plain_source_tree,
        module_name="backend",
        frid_context=SimpleNamespace(frid="1", linked_resources={}),
        run_state=SimpleNamespace(),
        get_required_modules_functionalities=lambda: {},
        unit_tests_running_context=UnitTestsRunningContext(fix_attempts=1),
        conformance_tests_running_context=SimpleNamespace(code_diff_files={"app.py": "@@ conformance fix @@"}),
    )


def memory_files_content_of(call):
    return call[7]


def conformance_fix_changes_of(call):
    return call[8]


def test_memory_is_passed_to_the_unit_test_fixer(render_context):
    FixUnitTests().execute(render_context, {"previous_unittests_issue": "AssertionError"})

    memory = memory_files_content_of(render_context.codeplain_api.calls[0])
    assert GLOBAL_MEMORY_FILE_NAME in memory


def test_conformance_changes_are_withheld_outside_the_conformance_loop(render_context):
    FixUnitTests().execute(render_context, {"previous_unittests_issue": "AssertionError"})

    assert conformance_fix_changes_of(render_context.codeplain_api.calls[0]) is None


def test_conformance_changes_are_supplied_inside_the_conformance_loop(render_context):
    FixUnitTests(inside_conformance_fix_loop=True).execute(
        render_context, {"previous_unittests_issue": "AssertionError"}
    )

    assert conformance_fix_changes_of(render_context.codeplain_api.calls[0]) == {"app.py": "@@ conformance fix @@"}


def test_the_round_is_held_for_the_next_run_to_record(render_context):
    FixUnitTests().execute(render_context, {"previous_unittests_issue": "AssertionError"})

    ctx = render_context.unit_tests_running_context
    assert ctx.previous_unittests_issue == "AssertionError"
    assert ctx.code_diff_files is not None


def test_only_the_conformance_loop_instance_is_marked_as_such():
    from render_machine.state_machine_config import StateMachineConfig

    marked = [
        state
        for state, action in StateMachineConfig().get_action_map().items()
        if isinstance(action, FixUnitTests) and action.inside_conformance_fix_loop
    ]

    assert len(marked) == 1
    assert "processingConformanceTests" in marked[0]
