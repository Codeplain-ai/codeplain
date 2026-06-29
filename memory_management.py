import json
import os
import shutil

import file_utils
from plain2code_console import console
from plain_modules import CODEPLAIN_MEMORY_SUBFOLDER
from render_machine.implementation_code_helpers import ImplementationCodeHelpers
from render_machine.render_context import RenderContext

CONFORMANCE_TESTS_SUCCESS_EXIT_CODE = 0
CONFORMANCE_TEST_MEMORY_SUBFOLDER = "conformance_test_memory"
AGENT_MEMORY_SUBFOLDER = "agent_memory"
# Learnings the fixing agent marked as project-wide. Unlike agent_memory (which is local to
# one module), notes here are copied into every module's memory so all modules' agents see
# them. Each module keeps its own committed copy, which preserves the per-module git model.
GLOBAL_MEMORY_SUBFOLDER = "global_memory"


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

    @staticmethod
    def fetch_agent_memory_files(memory_folder: str) -> dict[str, str]:
        """Fetch agent memory notes from memory_folder/agent_memory.

        These are free-form notes written by fixing agents via the write_memory tool.
        Unlike conformance test memories, they persist across functionalities and renders.
        """
        memory_path = os.path.join(memory_folder, AGENT_MEMORY_SUBFOLDER)
        if not os.path.exists(memory_path):
            return {}
        memory_files = file_utils.list_all_text_files(memory_path)
        memory_files_content = file_utils.get_existing_files_content(memory_path, memory_files)
        console.debug(f"Loaded {len(memory_files_content)} agent memory files.")
        return memory_files_content

    @staticmethod
    def list_memory_files(memory_folder: str) -> list[str]:
        """List memory note paths (relative to memory_folder) without reading their content.

        Used to make an agent aware of which notes exist so it can read just the relevant
        ones on demand via its file tools, instead of injecting every note's content into
        the prompt (which would balloon as notes accumulate). Covers both the
        conformance_test_memory and agent_memory subfolders.
        """
        if not os.path.exists(memory_folder):
            return []
        names = []
        for root, _dirs, files in os.walk(memory_folder):
            for file_name in files:
                full_path = os.path.join(root, file_name)
                names.append(os.path.relpath(full_path, memory_folder))
        return sorted(names)

    @staticmethod
    def memory_folder_for(conformance_tests_folder: str, module_name: str) -> str:
        """Return the .memory folder path for a module (the per-module memory root)."""
        return os.path.join(conformance_tests_folder, module_name, CODEPLAIN_MEMORY_SUBFOLDER)

    @staticmethod
    def write_agent_memory_file(
        memory_folder: str, file_name: str, content: str, subfolder: str = AGENT_MEMORY_SUBFOLDER
    ) -> str:
        """Write a memory note under memory_folder/<subfolder> and return its full path.

        Defaults to the agent_memory subfolder (module-local notes); pass
        GLOBAL_MEMORY_SUBFOLDER to write a project-wide note that sync_global_memories
        will propagate to every module.
        """
        memory_path = os.path.join(memory_folder, subfolder)
        os.makedirs(memory_path, exist_ok=True)
        full_path = os.path.join(memory_path, file_name)
        with open(full_path, "w", encoding="utf-8") as f:
            f.write(content)
        return full_path

    @staticmethod
    def sync_global_memories(target_memory_folder: str, source_memory_folders: list[str]) -> int:
        """Copy global memory notes from other modules into this module's global_memory folder.

        Notes are deduplicated by file name (file names are content-hashed at write time), so a
        note already present is left untouched and re-copying is a no-op. Returns the number of
        notes newly copied. Run before a module renders so its agents see project-wide learnings,
        and so each module commits its own copy (keeping the per-module git model intact).
        """
        target_global = os.path.join(target_memory_folder, GLOBAL_MEMORY_SUBFOLDER)
        normalized_target = os.path.normpath(os.path.abspath(target_global))
        copied = 0
        for source_memory_folder in source_memory_folders:
            source_global = os.path.join(source_memory_folder, GLOBAL_MEMORY_SUBFOLDER)
            if not os.path.isdir(source_global):
                continue
            if os.path.normpath(os.path.abspath(source_global)) == normalized_target:
                continue
            for file_name in os.listdir(source_global):
                source_file = os.path.join(source_global, file_name)
                if not os.path.isfile(source_file):
                    continue
                target_file = os.path.join(target_global, file_name)
                if os.path.exists(target_file):
                    continue
                os.makedirs(target_global, exist_ok=True)
                shutil.copyfile(source_file, target_file)
                copied += 1
        if copied:
            console.debug(f"Synced {copied} global memory note(s) into {target_global}.")
        return copied

    def __init__(self, codeplain_api, module_name: str, conformance_tests_folder: str):
        self.codeplain_api = codeplain_api
        self.memory_folder = MemoryManager.memory_folder_for(conformance_tests_folder, module_name)

    def create_conformance_tests_memory(
        self, render_context: RenderContext, exit_code: int, conformance_tests_issue: str
    ):

        current_conformance_tests_issue_frid = render_context.conformance_tests_running_context.current_testing_frid
        current_conformance_tests_issue_module = (
            render_context.conformance_tests_running_context.current_testing_module_name
        )
        old_conformance_tests_issue_frid = (
            render_context.conformance_tests_running_context.previous_conformance_tests_issue_frid
        )
        old_conformance_tests_issue_module = (
            render_context.conformance_tests_running_context.previous_conformance_tests_issue_module
        )

        old_conformance_tests_issue = (
            render_context.conformance_tests_running_context.previous_conformance_tests_issue_old
        )

        is_first_time_running_conformance_tests = (
            old_conformance_tests_issue_frid is None
            or old_conformance_tests_issue_frid == ""
            or old_conformance_tests_issue_module != current_conformance_tests_issue_module
        )
        is_same_frid_as_previous_failing_test = (
            current_conformance_tests_issue_frid == old_conformance_tests_issue_frid
            and current_conformance_tests_issue_module == old_conformance_tests_issue_module
        )
        is_conformance_test_failed = exit_code != CONFORMANCE_TESTS_SUCCESS_EXIT_CODE

        should_create_memory = not is_first_time_running_conformance_tests and (
            is_same_frid_as_previous_failing_test or is_conformance_test_failed
        )
        code_diff_files = render_context.conformance_tests_running_context.code_diff_files

        if not should_create_memory or code_diff_files is None:
            console.debug(
                "Skipping creation of conformance test memory because the conditions for creating memories are not met."
            )
            return

        existing_files, existing_files_content = ImplementationCodeHelpers.fetch_existing_files(
            render_context.build_folder
        )
        memory_files, memory_files_content = MemoryManager.fetch_memory_files(self.memory_folder)

        conformance_tests_folder_name = (
            render_context.conformance_tests_running_context.get_current_conformance_test_folder_name()
        )

        (
            _,
            existing_conformance_test_files_content,
        ) = render_context.conformance_tests.fetch_existing_conformance_test_files(
            render_context.module_name,
            render_context.required_modules,
            render_context.conformance_tests_running_context.current_testing_module_name,
            conformance_tests_folder_name,
        )
        acceptance_tests = render_context.conformance_tests_running_context.get_current_acceptance_tests()

        response_files = render_context.codeplain_api.create_conformance_test_memory(
            render_context.frid_context.frid,
            render_context.plain_source_tree,
            render_context.frid_context.linked_resources,
            existing_files_content,
            memory_files_content,
            render_context.module_name,
            render_context.get_required_modules_functionalities(),
            code_diff_files,
            existing_conformance_test_files_content,
            acceptance_tests,
            conformance_tests_issue,
            conformance_tests_folder_name,
            old_conformance_tests_issue,
            run_state=render_context.run_state,
        )
        if len(response_files) > 0:
            memory_folder_path = os.path.join(self.memory_folder, CONFORMANCE_TEST_MEMORY_SUBFOLDER)
            file_utils.store_response_files(memory_folder_path, response_files, memory_files)

    def delete_unresolved_memory_files(self):
        """Delete memory files whose resolution_status is not 'RESOLVED'."""
        memory_path = os.path.join(self.memory_folder, CONFORMANCE_TEST_MEMORY_SUBFOLDER)
        if not os.path.exists(memory_path):
            return

        memory_files = file_utils.list_all_text_files(memory_path)
        for file_name in memory_files:
            file_path = os.path.join(memory_path, file_name)
            try:
                with open(file_path, "r") as f:
                    content = json.load(f)
                if content.get("resolution_status") == "RESOLVED":
                    continue
                else:
                    os.remove(file_path)
            except (json.JSONDecodeError, OSError):
                # Not a valid JSON file, unlikely to be a valid memory file, delete it
                console.error(f"Memory file is not a valid JSON file: {file_name}. Deleting it.")
                os.remove(file_path)

            console.debug(f"Deleted temporary memory file: {file_name}")
