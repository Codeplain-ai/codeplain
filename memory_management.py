"""What the renderer remembers about fixing tests, and for how long.

Two journals, with two different lifetimes:

* The **functionality journal** - one record per round of a fix loop, in ``.memory/attempts/``. Both fix loops
  write into it, because a change made to satisfy a conformance test is exactly what the unit test loop is
  about to trip over. It is append-only: each round adds a file and touches no other, so what the loop has
  already tried stays readable for the rest of the loop. Discarded once the functionality's tests pass, at
  which point what is worth keeping has been moved into global memory.

* The **global memory** - one file, ``.memory/global_memory.json``, holding what transfers beyond the
  functionality that learned it. Rewritten in full by the consolidation step, which is the only place where
  something recorded earlier can be revised or retired. It is carried forward to the next module in the render
  chain, so a six module project learns each of these things once rather than six times.

Global memory is carried by copying the file forward rather than by sharing one file between modules. A shared
file would let a module inherit knowledge from its own future: re-render module three and it would read what
modules four onwards learned in a later render. Copying makes each module's file a snapshot of what was known
when that module was rendered, which keeps partial re-renders reproducible and the chain diffable - the
difference between two modules' files is exactly what the later one contributed.

This replaces an arrangement that rebuilt the memory after every conformance test run and, being told not to
duplicate itself, deleted the previous one each time - so a twenty round fix loop remembered one round.
"""

import os
import shutil
from typing import Optional

import file_utils
from plain2code_console import console
from render_machine.render_context import RenderContext

CONFORMANCE_TESTS_SUCCESS_EXIT_CODE = 0

ATTEMPTS_SUBFOLDER = "attempts"
GLOBAL_MEMORY_FILE_NAME = "global_memory.json"

TEST_SURFACE_CONFORMANCE_TESTS = "conformance_tests"
TEST_SURFACE_UNIT_TESTS = "unit_tests"

# Attempt records fed to a prompt, most recent last. The whole journal stays on disk - the cap is about what
# one prompt can usefully carry, not about what is worth keeping. A loop long enough to hit this has bigger
# problems than a missing early record, and the early rounds are the ones whose changes the current code
# already reflects.
MAX_ATTEMPTS_IN_PROMPT = 15


class MemoryManager:

    @staticmethod
    def fetch_memory_files(memory_folder: str) -> tuple[list[str], dict[str, str]]:
        """The memory as prompt input: global memory, then this functionality's journal in the order written.

        Read by name and by subfolder rather than by listing the memory folder whole, so that adding something
        to `.memory` later cannot silently start feeding it to every prompt.
        """
        memory_files = []
        if os.path.exists(os.path.join(memory_folder, GLOBAL_MEMORY_FILE_NAME)):
            memory_files.append(GLOBAL_MEMORY_FILE_NAME)

        for attempt_file_name in MemoryManager._attempt_file_names(memory_folder)[-MAX_ATTEMPTS_IN_PROMPT:]:
            memory_files.append(os.path.join(ATTEMPTS_SUBFOLDER, attempt_file_name))

        memory_files_content = file_utils.get_existing_files_content(memory_folder, memory_files)
        console.debug(f"Loaded {len(memory_files_content)} memory files.")
        return memory_files, memory_files_content

    @staticmethod
    def _attempt_file_names(memory_folder: str) -> list[str]:
        """The journal's file names in the order they were written. Names are zero padded so that sorting them
        as text is the same as sorting them by round."""
        attempts_path = os.path.join(memory_folder, ATTEMPTS_SUBFOLDER)
        if not os.path.exists(attempts_path):
            return []
        return sorted(file_utils.list_all_text_files(attempts_path))

    def __init__(self, codeplain_api, memory_folder: str, predecessor_memory_folder: Optional[str] = None):
        self.codeplain_api = codeplain_api
        self.memory_folder = memory_folder
        self.predecessor_memory_folder = predecessor_memory_folder

    def inherit_global_memory(self) -> None:
        """Carry global memory forward from the module rendered before this one.

        Copied rather than merged: the predecessor's file already holds everything inherited from further back,
        so one copy carries the whole chain.

        Called once the module's folders have been prepared, not before. Preparing them deletes the module
        folder outright, `.memory` included, so a copy made any earlier would not survive to be read.
        """
        if not self.predecessor_memory_folder:
            return

        source = os.path.join(self.predecessor_memory_folder, GLOBAL_MEMORY_FILE_NAME)
        if not os.path.exists(source):
            console.debug(f"No global memory to inherit from {self.predecessor_memory_folder}.")
            return

        try:
            os.makedirs(self.memory_folder, exist_ok=True)
            shutil.copyfile(source, os.path.join(self.memory_folder, GLOBAL_MEMORY_FILE_NAME))
            console.debug(f"Inherited global memory from {source}.")
        except OSError as exception:
            console.debug(f"Could not inherit global memory from {source}: {exception}.")

    def clear_functionality_journal(self) -> None:
        """Drop the journal. Called when a functionality is done with it - either because its lessons have been
        consolidated, or because a new functionality is starting and the old rounds describe a problem that is
        no longer the one being worked on."""
        attempts_path = os.path.join(self.memory_folder, ATTEMPTS_SUBFOLDER)
        if os.path.exists(attempts_path):
            file_utils.delete_folder(attempts_path)

    def record_conformance_test_fix_attempt(
        self, render_context: RenderContext, exit_code: int, conformance_tests_issue: str
    ) -> None:
        """Record the conformance round that has just been re-tested.

        Recorded whenever a change was applied in response to a previous failure, including the round that
        finally passes - a round whose failure is gone is the one a later loop most needs to know about. The
        change and the outcome are not required to concern the same test: a fix for one functionality that
        breaks another's tests is a round worth having on record, and the record says so in as many words.
        """
        ctx = render_context.conformance_tests_running_context
        previous_conformance_tests_issue = ctx.previous_conformance_tests_issue_old
        code_diff_files = ctx.code_diff_files

        if not previous_conformance_tests_issue or code_diff_files is None:
            console.debug("No conformance test fix was applied before this run, so there is no round to record.")
            return

        conformance_tests_folder_name = ctx.get_current_conformance_test_folder_name()
        (
            _,
            conformance_tests_files_content,
        ) = render_context.conformance_tests.fetch_existing_conformance_test_files(
            render_context.module_name,
            render_context.required_modules,
            ctx.current_testing_module_name,
            conformance_tests_folder_name,
        )

        self._record_attempt(
            render_context,
            TEST_SURFACE_CONFORMANCE_TESTS,
            previous_conformance_tests_issue,
            "" if exit_code == CONFORMANCE_TESTS_SUCCESS_EXIT_CODE else conformance_tests_issue,
            code_diff_files,
            conformance_tests_files_content,
            ctx.get_current_acceptance_tests(),
            conformance_tests_folder_name,
        )

    def record_unit_test_fix_attempt(self, render_context: RenderContext, exit_code: int, unittests_issue: str) -> None:
        """Record the unit test round that has just been re-tested.

        The unit test loop has no conformance suite of its own, so the conformance arguments are empty and the
        prompt is told which surface it is describing. The record still goes into the same journal, because the
        two loops act on the same implementation and each needs to see what the other did to it.
        """
        ctx = render_context.unit_tests_running_context
        if not ctx.previous_unittests_issue or ctx.code_diff_files is None:
            console.debug("No unit test fix was applied before this run, so there is no round to record.")
            return

        self._record_attempt(
            render_context,
            TEST_SURFACE_UNIT_TESTS,
            ctx.previous_unittests_issue,
            "" if exit_code == 0 else unittests_issue,
            ctx.code_diff_files,
            {},
            None,
            None,
        )

    def _record_attempt(
        self,
        render_context: RenderContext,
        test_surface: str,
        previous_test_issue: str,
        current_test_issue: str,
        code_diff_files: dict,
        conformance_tests_files_content: dict,
        acceptance_tests: Optional[list],
        conformance_tests_folder_name: Optional[str],
    ) -> None:
        memory_files, memory_files_content = MemoryManager.fetch_memory_files(self.memory_folder)
        attempt_file_name = self._next_attempt_file_name()

        response_files = render_context.codeplain_api.create_conformance_test_memory(
            render_context.frid_context.frid,
            render_context.plain_source_tree,
            render_context.frid_context.linked_resources,
            {},
            memory_files_content,
            render_context.module_name,
            render_context.get_required_modules_functionalities(),
            code_diff_files,
            conformance_tests_files_content,
            acceptance_tests,
            current_test_issue,
            conformance_tests_folder_name,
            previous_test_issue,
            test_surface,
            attempt_file_name,
            run_state=render_context.run_state,
        )

        if not response_files:
            return

        # Stored with no existing-file list, so nothing already in the journal can be overwritten or removed by
        # this round - which is what makes the journal append-only rather than merely intended to be.
        attempts_path = os.path.join(self.memory_folder, ATTEMPTS_SUBFOLDER)
        file_utils.store_response_files(attempts_path, response_files, [])
        console.debug(f"Recorded {test_surface} fix attempt in {attempt_file_name}.")

    def _next_attempt_file_name(self) -> str:
        return f"attempt_{len(MemoryManager._attempt_file_names(self.memory_folder)) + 1:03d}.json"

    def consolidate_global_memory(self, render_context: RenderContext) -> None:
        """Move what transfers out of the journal and into global memory, then discard the journal.

        Called when a functionality's own conformance tests pass. A functionality whose tests passed with no
        fixing has an empty journal and teaches nothing, so no call is made for it.
        """
        ctx = render_context.conformance_tests_running_context
        if not MemoryManager._attempt_file_names(self.memory_folder):
            console.debug("No test fixes were needed, so there is nothing to consolidate.")
            return

        memory_files, memory_files_content = MemoryManager.fetch_memory_files(self.memory_folder)
        conformance_tests_folder_name = ctx.get_current_conformance_test_folder_name()
        (
            _,
            conformance_tests_files_content,
        ) = render_context.conformance_tests.fetch_existing_conformance_test_files(
            render_context.module_name,
            render_context.required_modules,
            ctx.current_testing_module_name,
            conformance_tests_folder_name,
        )

        console.info(
            f"Consolidating what fixing the tests for functionality {ctx.current_testing_frid} "
            f"in module {ctx.current_testing_module_name} has taught."
        )

        response_files = render_context.codeplain_api.consolidate_global_memory(
            render_context.frid_context.frid,
            render_context.plain_source_tree,
            render_context.frid_context.linked_resources,
            memory_files_content,
            render_context.module_name,
            render_context.get_required_modules_functionalities(),
            conformance_tests_files_content,
            ctx.get_current_acceptance_tests(),
            conformance_tests_folder_name,
            run_state=render_context.run_state,
        )

        if response_files:
            existing_global_memory = [GLOBAL_MEMORY_FILE_NAME] if GLOBAL_MEMORY_FILE_NAME in memory_files else []
            file_utils.store_response_files(self.memory_folder, response_files, existing_global_memory)

        # The journal has served its purpose for this functionality; whatever was worth keeping is now global.
        self.clear_functionality_journal()
