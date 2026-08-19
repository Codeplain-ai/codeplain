"""What the renderer remembers about fixing tests, and for how long.

Three artifacts, with three different lifetimes:

* The **journal**, written mechanically as a functionality's fix loop runs, holding every failure observed and
  every change applied. See :mod:`conformance_test_journal`. Discarded once that functionality's tests pass.

* The **module lessons**, extracted from the journal at that point. These belong to the module that learned
  them and are fed into every later prompt for it, which is why the set is capped and why a lesson has to be a
  transferable constraint rather than the story of one bug.

* The **project lessons**, the subset of those lessons that would still hold if this module were deleted and a
  different one written in its place - parent POMs, dependency scopes, logging binders, framework idioms. They
  are inherited by the next module in the render chain, so a six module project learns each of them once
  instead of six times.

Project lessons are carried by copying the file forward rather than by sharing one file between modules. A
shared file would let a module inherit knowledge from its own future: re-render module three and it would read
what modules four onwards learned in a later render. Copying makes each module's file a snapshot of what was
known when that module was rendered, which keeps partial re-renders deterministic and makes the chain
diffable - the difference between two modules' files is exactly what the later one contributed.

Consolidation runs once per functionality. It replaces an earlier arrangement that rebuilt a memory after
every conformance test run and, being told not to duplicate itself, deleted the previous one each time - so a
twenty round fix loop remembered only its most recent round.
"""

import os
import shutil
from typing import Optional

import file_utils
from conformance_test_journal import ConformanceTestJournal, compute_spec_hash
from plain2code_console import console
from render_machine.render_context import RenderContext

CONFORMANCE_TESTS_SUCCESS_EXIT_CODE = 0

# The lessons this module learned, and the ones it inherited and may add to. Both are fed to prompts.
#
# Read by name rather than by listing the folder. The folder also holds the fix journal and the boilerplate
# profile, neither of which may reach a prompt - the journal because it is handed over explicitly to the one
# step that acts on it, the profile because it is hashes. Relying on those living in different directories
# made the boundary positional; naming the files that belong in a prompt makes it explicit.
CONFORMANCE_TEST_LESSONS_FILE_NAME = "conformance_test_lessons.json"
PROJECT_LESSONS_FILE_NAME = "project_lessons.json"

MEMORY_FILE_NAMES = (CONFORMANCE_TEST_LESSONS_FILE_NAME, PROJECT_LESSONS_FILE_NAME)


class MemoryManager:

    @staticmethod
    def fetch_memory_files(memory_folder: str) -> tuple[list[str], dict[str, str]]:
        """The lessons held for this module, as prompt input."""
        memory_files = [
            file_name for file_name in MEMORY_FILE_NAMES if os.path.exists(os.path.join(memory_folder, file_name))
        ]
        memory_files_content = file_utils.get_existing_files_content(memory_folder, memory_files)
        console.debug(f"Loaded {len(memory_files_content)} memory files.")
        return memory_files, memory_files_content

    @staticmethod
    def inherit_project_lessons(predecessor_memory_folder: Optional[str], memory_folder: str) -> None:
        """Carry the project lessons forward from the module rendered before this one.

        Copied rather than merged: the predecessor's file already contains everything inherited from further
        back, so one copy carries the whole chain. Overwriting is deliberate - the file is a snapshot of what
        the chain knew at this point, not an accumulation of this module's own history.

        With no predecessor, or none that has a file, whatever is already here is left alone. That lets the
        first module in a chain keep accumulating across renders, which is where a project's toolchain facts
        get their first home.
        """
        if not predecessor_memory_folder:
            return

        source = os.path.join(predecessor_memory_folder, PROJECT_LESSONS_FILE_NAME)
        if not os.path.exists(source):
            return

        destination = os.path.join(memory_folder, PROJECT_LESSONS_FILE_NAME)
        try:
            os.makedirs(memory_folder, exist_ok=True)
            shutil.copyfile(source, destination)
            console.debug(f"Inherited project lessons from {source}.")
        except OSError as exception:
            console.debug(f"Could not inherit project lessons from {source}: {exception}.")

    def __init__(self, codeplain_api, memory_folder: str, project_memory_folder: str):
        self.codeplain_api = codeplain_api
        self.memory_folder = memory_folder
        self.project_memory_folder = project_memory_folder

    def consolidate_lessons(self, render_context: RenderContext) -> None:
        """Extract what transfers to later functionalities, then discard the journal it came from.

        Called when a functionality's own conformance tests pass. A functionality whose tests passed without
        any fixing has an empty journal and teaches nothing, so no call is made for it.
        """
        ctx = render_context.conformance_tests_running_context
        if ctx.current_testing_frid is None:
            return

        journal = ConformanceTestJournal.load(
            self.memory_folder,
            ctx.current_testing_module_name,
            ctx.current_testing_frid,
            spec_hash=compute_spec_hash(ctx.current_testing_frid_specifications),
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
            file_utils.store_response_files(self.memory_folder, response_files, memory_files)

        # The journal has served its purpose for this functionality; anything worth keeping is now a lesson.
        journal.delete(self.memory_folder)
