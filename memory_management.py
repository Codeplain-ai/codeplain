import json
import os

import file_utils
from plain2code_console import console
from plain_modules import CODEPLAIN_MEMORY_SUBFOLDER
from render_machine.implementation_code_helpers import ImplementationCodeHelpers
from render_machine.render_context import RenderContext

CONFORMANCE_TESTS_SUCCESS_EXIT_CODE = 0
CONFORMANCE_TEST_MEMORY_SUBFOLDER = "conformance_test_memory"


class MemoryManager:

    @staticmethod
    def fetch_memory_files(memory_folder: str):
        """Fetch memory files from memory_folder/conformance_test_memory."""
        memory_path = os.path.join(memory_folder, CONFORMANCE_TEST_MEMORY_SUBFOLDER)
        if not os.path.exists(memory_path):
            return [], {}
        memory_files = file_utils.list_all_text_files(memory_path)
        memory_files_content = file_utils.get_existing_files_content(memory_path, memory_files)
        console.info(f"Loaded {len(memory_files_content)} memory files.")
        return memory_files, memory_files_content

    def __init__(self, codeplain_api, module_build_folder: str):
        self.codeplain_api = codeplain_api
        self.memory_folder = os.path.join(module_build_folder, CODEPLAIN_MEMORY_SUBFOLDER)

    def create_conformance_tests_memory(
        self, render_context: RenderContext, exit_code: int, conformance_tests_issue: str
    ):

        current_conformance_tests_issue_frid = render_context.conformance_tests_running_context.current_testing_frid
        old_conformance_tests_issue_frid = (
            render_context.conformance_tests_running_context.previous_conformance_tests_issue_frid
        )

        old_conformance_tests_issue = (
            render_context.conformance_tests_running_context.previous_conformance_tests_issue_old
        )

        is_first_time_running_conformance_tests = (
            old_conformance_tests_issue_frid is None or old_conformance_tests_issue_frid == ""
        )
        is_same_frid_as_previous_failing_test = current_conformance_tests_issue_frid == old_conformance_tests_issue_frid
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
