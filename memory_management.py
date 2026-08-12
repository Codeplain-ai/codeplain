"""What the renderer remembers about conformance tests, and when.

Two artifacts, with different lifetimes:

* The **journal**, written mechanically as a functionality's fix loop runs, holding every attempt made against
  it. See :mod:`conformance_test_journal`. It is discarded once that functionality's tests pass.

* The **lessons**, extracted from the journal at that point and kept for the rest of the project. These are
  the memory files, and they are fed into every later prompt - which is why the set is capped and why a lesson
  has to be a transferable constraint rather than the story of one bug.

Consolidation runs once per functionality. It replaces an earlier arrangement that rebuilt a memory after
every conformance test run and, being told not to duplicate itself, deleted the previous one each time - so a
twenty round fix loop remembered only its most recent round.
"""

import os

import file_utils
from conformance_test_journal import ConformanceTestJournal
from plain2code_console import console
from render_machine.render_context import RenderContext

CONFORMANCE_TESTS_SUCCESS_EXIT_CODE = 0
CONFORMANCE_TEST_MEMORY_SUBFOLDER = "conformance_test_memory"


class MemoryManager:

    @staticmethod
    def fetch_memory_files(memory_folder: str) -> tuple[list[str], dict[str, str]]:
        """Fetch memory files from memory_folder/conformance_test_memory."""
        memory_path = os.path.join(memory_folder, CONFORMANCE_TEST_MEMORY_SUBFOLDER)
        if not os.path.exists(memory_path):
            return [], {}
        memory_files = file_utils.list_all_text_files(memory_path)
        memory_files_content = file_utils.get_existing_files_content(memory_path, memory_files)
        console.debug(f"Loaded {len(memory_files_content)} memory files.")
        return memory_files, memory_files_content

    def __init__(self, codeplain_api, memory_folder: str):
        self.codeplain_api = codeplain_api
        self.memory_folder = memory_folder

    def consolidate_lessons(self, render_context: RenderContext) -> None:
        """Extract what transfers to later functionalities, then discard the journal it came from.

        Called when a functionality's own conformance tests pass. A functionality whose tests passed without
        any fixing has an empty journal and teaches nothing, so no call is made for it.
        """
        ctx = render_context.conformance_tests_running_context
        if ctx.current_testing_frid is None:
            return

        journal = ConformanceTestJournal.load(
            self.memory_folder, ctx.current_testing_module_name, ctx.current_testing_frid
        )
        previous_attempts = journal.render_for_prompt()
        if not previous_attempts:
            console.debug("No conformance test fixes were needed, so there are no lessons to consolidate.")
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
            f"Consolidating what fixing the conformance tests for functionality {ctx.current_testing_frid} "
            f"in module {ctx.current_testing_module_name} has taught."
        )

        response_files = render_context.codeplain_api.consolidate_conformance_test_lessons(
            render_context.frid_context.frid,
            render_context.plain_source_tree,
            render_context.frid_context.linked_resources,
            memory_files_content,
            render_context.module_name,
            render_context.get_required_modules_functionalities(),
            conformance_tests_files_content,
            ctx.get_current_acceptance_tests(),
            conformance_tests_folder_name,
            previous_attempts,
            run_state=render_context.run_state,
        )

        if response_files:
            memory_folder_path = os.path.join(self.memory_folder, CONFORMANCE_TEST_MEMORY_SUBFOLDER)
            file_utils.store_response_files(memory_folder_path, response_files, memory_files)

        # The journal has served its purpose for this functionality; anything worth keeping is now a lesson.
        journal.delete(self.memory_folder)
