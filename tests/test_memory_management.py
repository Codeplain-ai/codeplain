"""Tests for the two memory journals: the append-only functionality journal, and global memory.

What is being pinned down here is mostly what *cannot* happen. The arrangement this replaced rebuilt the memory
after every test run and deleted what it had written before, so a long fix loop arrived at its last round
remembering only the previous one. The tests below assert that recording a round leaves every earlier round in
place, and that removal happens only where it is deliberate: consolidation, and the start of a functionality.
"""

import json
import os
from types import SimpleNamespace

import pytest

from memory_management import (
    ATTEMPTS_SUBFOLDER,
    GLOBAL_MEMORY_FILE_NAME,
    MAX_ATTEMPTS_IN_PROMPT,
    TEST_SURFACE_CONFORMANCE_TESTS,
    TEST_SURFACE_UNIT_TESTS,
    MemoryManager,
)


class FakeApi:
    """Stands in for the API, returning a record for whatever file name it is asked to write to."""

    def __init__(self, global_memory_learnings=None):
        self.attempt_calls = []
        self.consolidate_calls = []
        self.global_memory_learnings = global_memory_learnings if global_memory_learnings is not None else []

    def create_conformance_test_memory(self, *args, **kwargs):
        test_surface, attempt_file_name = args[-2], args[-1]
        self.attempt_calls.append({"test_surface": test_surface, "file_name": attempt_file_name, "args": args})
        return {attempt_file_name: json.dumps({"test_surface": test_surface, "failure": "a failure"})}

    def consolidate_global_memory(self, *args, **kwargs):
        self.consolidate_calls.append(args)
        return {GLOBAL_MEMORY_FILE_NAME: json.dumps({"learnings": self.global_memory_learnings})}


def make_render_context(api, memory_folder, exit_code_issue="a failure", with_previous_fix=True):
    conformance_context = SimpleNamespace(
        previous_conformance_tests_issue_old=exit_code_issue if with_previous_fix else None,
        code_diff_files={"src/app.py": "@@ diff @@"} if with_previous_fix else None,
        current_testing_module_name="backend",
        current_testing_frid="1",
        get_current_conformance_test_folder_name=lambda: "test_login",
        get_current_acceptance_tests=lambda: ["a user can log in"],
    )
    unit_context = SimpleNamespace(
        previous_unittests_issue=exit_code_issue if with_previous_fix else None,
        code_diff_files={"src/app.py": "@@ diff @@"} if with_previous_fix else None,
    )
    return SimpleNamespace(
        codeplain_api=api,
        frid_context=SimpleNamespace(frid="1", linked_resources={}),
        plain_source_tree={},
        module_name="backend",
        required_modules=[],
        run_state=SimpleNamespace(),
        get_required_modules_functionalities=lambda: {},
        conformance_tests_running_context=conformance_context,
        unit_tests_running_context=unit_context,
        conformance_tests=SimpleNamespace(fetch_existing_conformance_test_files=lambda *args: ([], {})),
    )


@pytest.fixture
def memory_folder(tmp_path):
    return str(tmp_path / "module" / ".memory")


def attempt_files(memory_folder):
    attempts_path = os.path.join(memory_folder, ATTEMPTS_SUBFOLDER)
    return sorted(os.listdir(attempts_path)) if os.path.exists(attempts_path) else []


class TestJournalIsAppendOnly:
    def test_each_round_adds_a_file_and_keeps_the_earlier_ones(self, memory_folder):
        api = FakeApi()
        manager = MemoryManager(api, memory_folder)
        render_context = make_render_context(api, memory_folder)

        for _ in range(3):
            manager.record_conformance_test_fix_attempt(render_context, 1, "still failing")

        assert attempt_files(memory_folder) == ["attempt_001.json", "attempt_002.json", "attempt_003.json"]

    def test_round_that_finally_passes_is_recorded_too(self, memory_folder):
        api = FakeApi()
        manager = MemoryManager(api, memory_folder)
        render_context = make_render_context(api, memory_folder)

        manager.record_conformance_test_fix_attempt(render_context, 0, "")

        assert attempt_files(memory_folder) == ["attempt_001.json"]
        # A passing run has no current issue, and that absence is what says the failure is gone.
        assert api.attempt_calls[0]["args"][10] == ""

    def test_nothing_is_recorded_when_no_fix_preceded_the_run(self, memory_folder):
        api = FakeApi()
        manager = MemoryManager(api, memory_folder)
        render_context = make_render_context(api, memory_folder, with_previous_fix=False)

        manager.record_conformance_test_fix_attempt(render_context, 1, "a failure")
        manager.record_unit_test_fix_attempt(render_context, 1, "a failure")

        assert attempt_files(memory_folder) == []
        assert api.attempt_calls == []

    def test_both_fix_loops_write_into_the_same_journal(self, memory_folder):
        api = FakeApi()
        manager = MemoryManager(api, memory_folder)
        render_context = make_render_context(api, memory_folder)

        manager.record_conformance_test_fix_attempt(render_context, 1, "still failing")
        manager.record_unit_test_fix_attempt(render_context, 1, "still failing")

        assert attempt_files(memory_folder) == ["attempt_001.json", "attempt_002.json"]
        assert [call["test_surface"] for call in api.attempt_calls] == [
            TEST_SURFACE_CONFORMANCE_TESTS,
            TEST_SURFACE_UNIT_TESTS,
        ]

    def test_unit_test_round_carries_no_conformance_suite(self, memory_folder):
        api = FakeApi()
        manager = MemoryManager(api, memory_folder)
        render_context = make_render_context(api, memory_folder)

        manager.record_unit_test_fix_attempt(render_context, 1, "still failing")

        conformance_tests_files, acceptance_tests, _, folder_name = api.attempt_calls[0]["args"][8:12]
        assert conformance_tests_files == {}
        assert acceptance_tests is None
        assert folder_name is None


class TestFetchMemoryFiles:
    def test_global_memory_comes_first_then_the_journal_in_order(self, memory_folder):
        api = FakeApi()
        manager = MemoryManager(api, memory_folder)
        os.makedirs(memory_folder, exist_ok=True)
        with open(os.path.join(memory_folder, GLOBAL_MEMORY_FILE_NAME), "w") as global_memory:
            global_memory.write('{"learnings": []}')
        render_context = make_render_context(api, memory_folder)
        for _ in range(2):
            manager.record_conformance_test_fix_attempt(render_context, 1, "still failing")

        memory_files, memory_files_content = MemoryManager.fetch_memory_files(memory_folder)

        assert memory_files == [
            GLOBAL_MEMORY_FILE_NAME,
            os.path.join(ATTEMPTS_SUBFOLDER, "attempt_001.json"),
            os.path.join(ATTEMPTS_SUBFOLDER, "attempt_002.json"),
        ]
        assert len(memory_files_content) == 3

    def test_only_the_most_recent_rounds_reach_a_prompt(self, memory_folder):
        api = FakeApi()
        manager = MemoryManager(api, memory_folder)
        render_context = make_render_context(api, memory_folder)
        for _ in range(MAX_ATTEMPTS_IN_PROMPT + 4):
            manager.record_conformance_test_fix_attempt(render_context, 1, "still failing")

        memory_files, _ = MemoryManager.fetch_memory_files(memory_folder)

        # Capped for the prompt, but the whole journal is still on disk.
        assert len(memory_files) == MAX_ATTEMPTS_IN_PROMPT
        assert memory_files[-1].endswith(f"attempt_{MAX_ATTEMPTS_IN_PROMPT + 4:03d}.json")
        assert len(attempt_files(memory_folder)) == MAX_ATTEMPTS_IN_PROMPT + 4

    def test_other_files_in_the_memory_folder_are_not_fed_to_prompts(self, memory_folder):
        os.makedirs(memory_folder, exist_ok=True)
        with open(os.path.join(memory_folder, "scratch.json"), "w") as scratch:
            scratch.write("{}")

        memory_files, _ = MemoryManager.fetch_memory_files(memory_folder)

        assert memory_files == []

    def test_empty_memory_folder_reads_as_empty(self, memory_folder):
        assert MemoryManager.fetch_memory_files(memory_folder) == ([], {})


class TestConsolidation:
    def test_writes_global_memory_and_discards_the_journal(self, memory_folder):
        api = FakeApi(global_memory_learnings=[{"learning": "The build needs the logging binder", "kind": "impl"}])
        manager = MemoryManager(api, memory_folder)
        render_context = make_render_context(api, memory_folder)
        manager.record_conformance_test_fix_attempt(render_context, 1, "still failing")

        manager.consolidate_global_memory(render_context)

        stored = json.load(open(os.path.join(memory_folder, GLOBAL_MEMORY_FILE_NAME)))
        assert stored["learnings"][0]["learning"] == "The build needs the logging binder"
        assert attempt_files(memory_folder) == []

    def test_is_skipped_when_the_functionality_needed_no_fixing(self, memory_folder):
        api = FakeApi()
        manager = MemoryManager(api, memory_folder)

        manager.consolidate_global_memory(make_render_context(api, memory_folder))

        assert api.consolidate_calls == []
        assert not os.path.exists(os.path.join(memory_folder, GLOBAL_MEMORY_FILE_NAME))

    def test_replaces_rather_than_appends_to_existing_global_memory(self, memory_folder):
        api = FakeApi(global_memory_learnings=[{"learning": "the surviving one", "kind": "impl"}])
        manager = MemoryManager(api, memory_folder)
        os.makedirs(memory_folder, exist_ok=True)
        with open(os.path.join(memory_folder, GLOBAL_MEMORY_FILE_NAME), "w") as global_memory:
            json.dump({"learnings": [{"learning": "the retired one", "kind": "impl"}]}, global_memory)
        render_context = make_render_context(api, memory_folder)
        manager.record_conformance_test_fix_attempt(render_context, 1, "still failing")

        manager.consolidate_global_memory(render_context)

        stored = json.load(open(os.path.join(memory_folder, GLOBAL_MEMORY_FILE_NAME)))
        assert [entry["learning"] for entry in stored["learnings"]] == ["the surviving one"]


class TestJournalLifetime:
    def test_a_new_functionality_starts_with_an_empty_journal(self, memory_folder):
        api = FakeApi()
        manager = MemoryManager(api, memory_folder)
        manager.record_conformance_test_fix_attempt(make_render_context(api, memory_folder), 1, "still failing")

        manager.clear_functionality_journal()

        assert attempt_files(memory_folder) == []

    def test_clearing_an_absent_journal_is_harmless(self, memory_folder):
        MemoryManager(FakeApi(), memory_folder).clear_functionality_journal()

    def test_clearing_the_journal_leaves_global_memory_alone(self, memory_folder):
        os.makedirs(memory_folder, exist_ok=True)
        with open(os.path.join(memory_folder, GLOBAL_MEMORY_FILE_NAME), "w") as global_memory:
            global_memory.write('{"learnings": []}')

        MemoryManager(FakeApi(), memory_folder).clear_functionality_journal()

        assert os.path.exists(os.path.join(memory_folder, GLOBAL_MEMORY_FILE_NAME))


class TestGlobalMemoryInheritance:
    def test_carried_forward_from_the_predecessor(self, tmp_path):
        predecessor = str(tmp_path / "one" / ".memory")
        os.makedirs(predecessor)
        with open(os.path.join(predecessor, GLOBAL_MEMORY_FILE_NAME), "w") as global_memory:
            json.dump({"learnings": [{"learning": "inherited", "kind": "impl"}]}, global_memory)
        successor = str(tmp_path / "two" / ".memory")

        MemoryManager(FakeApi(), successor, predecessor).inherit_global_memory()

        stored = json.load(open(os.path.join(successor, GLOBAL_MEMORY_FILE_NAME)))
        assert stored["learnings"][0]["learning"] == "inherited"

    def test_copied_rather_than_shared_so_a_module_cannot_read_its_own_future(self, tmp_path):
        predecessor = str(tmp_path / "one" / ".memory")
        os.makedirs(predecessor)
        with open(os.path.join(predecessor, GLOBAL_MEMORY_FILE_NAME), "w") as global_memory:
            global_memory.write('{"learnings": []}')
        successor = str(tmp_path / "two" / ".memory")
        MemoryManager(FakeApi(), successor, predecessor).inherit_global_memory()

        with open(os.path.join(successor, GLOBAL_MEMORY_FILE_NAME), "w") as global_memory:
            global_memory.write('{"learnings": [{"learning": "learned later"}]}')

        assert json.load(open(os.path.join(predecessor, GLOBAL_MEMORY_FILE_NAME))) == {"learnings": []}

    def test_first_module_in_the_chain_inherits_nothing(self, memory_folder):
        MemoryManager(FakeApi(), memory_folder).inherit_global_memory()

        assert not os.path.exists(os.path.join(memory_folder, GLOBAL_MEMORY_FILE_NAME))

    def test_predecessor_without_global_memory_is_not_an_error(self, tmp_path):
        predecessor = str(tmp_path / "one" / ".memory")
        os.makedirs(predecessor)
        successor = str(tmp_path / "two" / ".memory")

        MemoryManager(FakeApi(), successor, predecessor).inherit_global_memory()

        assert not os.path.exists(os.path.join(successor, GLOBAL_MEMORY_FILE_NAME))


class TestPredecessorInTheRenderChain:
    """The chain order comes from the module tree, not from what has been rendered, so a module skipped as
    unchanged still passes its global memory on to the module after it."""

    def _renderer(self, plain_module):
        from module_renderer import ModuleRenderer

        return ModuleRenderer(None, plain_module, None, None, SimpleNamespace(), None, None)

    def test_first_module_has_no_predecessor(self, get_test_data_path, tmp_path):
        from plain_modules import PlainModule

        fixtures = get_test_data_path("data/partial_rendering")
        root = PlainModule("pr_root.plain", str(tmp_path), [fixtures])
        first = root.all_required_modules[0]

        assert self._renderer(root)._predecessor_memory_folder(first) is None

    def test_each_module_follows_the_one_rendered_before_it(self, get_test_data_path, tmp_path):
        from plain_modules import PlainModule

        fixtures = get_test_data_path("data/partial_rendering")
        root = PlainModule("pr_root.plain", str(tmp_path), [fixtures])
        renderer = self._renderer(root)
        chain = root.all_required_modules + [root]

        for position, module in enumerate(chain[1:], start=1):
            assert renderer._predecessor_memory_folder(module) == chain[position - 1].module_memory_folder
