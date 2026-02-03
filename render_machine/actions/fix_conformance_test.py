from typing import Any

import diff_utils
import file_utils
import plain_spec
from memory_management import MemoryManager
from plain2code_console import console
from plain2code_exceptions import InternalClientError
from render_machine.actions.base_action import BaseAction
from render_machine.implementation_code_helpers import ImplementationCodeHelpers
from render_machine.render_context import RenderContext


class FixConformanceTest(BaseAction):
    IMPLEMENTATION_CODE_NOT_UPDATED = "implementation_code_not_updated"
    IMPLEMENTATION_CODE_UPDATED = "implementation_code_updated"

    def execute(self, render_context: RenderContext, previous_action_payload: Any | None):
        console.info(
            f"Fixing conformance test for functional requirement {render_context.conformance_tests_running_context.current_testing_frid} in module {render_context.conformance_tests_running_context.current_testing_module_name}."
        )

        if not previous_action_payload.get("previous_conformance_tests_issue"):
            raise InternalClientError("Previous action payload does not contain previous conformance tests issue.")
        previous_conformance_tests_issue = previous_action_payload["previous_conformance_tests_issue"]

        render_context.conformance_tests_running_context.previous_conformance_tests_issue_old = (
            previous_conformance_tests_issue
        )
        render_context.conformance_tests_running_context.previous_conformance_tests_issue_frid = (
            render_context.conformance_tests_running_context.current_testing_frid
        )

        if render_context.conformance_tests_running_context.current_testing_frid == render_context.frid_context.frid:
            console_message = f"Fixing conformance test for functional requirement {render_context.conformance_tests_running_context.current_testing_frid} in module {render_context.conformance_tests_running_context.current_testing_module_name}."
        else:
            console_message = f"While implementing functional requirement {render_context.frid_context.frid}, conformance tests for functional requirement {render_context.conformance_tests_running_context.current_testing_frid} in module {render_context.conformance_tests_running_context.current_testing_module_name} broke. Fixing them..."

        existing_files, existing_files_content = ImplementationCodeHelpers.fetch_existing_files(
            render_context.build_folder
        )
        _, memory_files_content = MemoryManager.fetch_memory_files(render_context.memory_manager.memory_folder)
        (
            existing_conformance_test_files,
            existing_conformance_test_files_content,
        ) = render_context.conformance_tests.fetch_existing_conformance_test_files(
            render_context.module_name,
            render_context.required_modules,
            render_context.conformance_tests_running_context.current_testing_module_name,
            render_context.conformance_tests_running_context.get_current_conformance_test_folder_name(),
        )
        previous_frid_code_diff = ImplementationCodeHelpers.get_code_diff(
            render_context.build_folder, render_context.plain_source_tree, render_context.frid_context.frid
        )

        if render_context.verbose:
            tmp_resources_list = []
            plain_spec.collect_linked_resources(
                render_context.plain_source_tree,
                tmp_resources_list,
                None,
                False,
                render_context.frid_context.frid,
            )
            console.print_resources(tmp_resources_list, render_context.frid_context.linked_resources)

            console.print_files(
                "Implementation files sent as input for fixing conformance tests issues:",
                render_context.build_folder,
                existing_files_content,
                style=console.INPUT_STYLE,
            )

            console.print_files(
                "Conformance tests files sent as input for fixing conformance tests issues:",
                render_context.conformance_tests_running_context.get_current_conformance_test_folder_name(),
                existing_conformance_test_files_content,
                style=console.INPUT_STYLE,
            )

        with console.status(console_message):
            [conformance_tests_fixed, response_files] = render_context.codeplain_api.fix_conformance_tests_issue(
                render_context.frid_context.frid,
                render_context.conformance_tests_running_context.current_testing_frid,
                render_context.plain_source_tree,
                render_context.frid_context.linked_resources,
                existing_files_content,
                memory_files_content,
                render_context.module_name,
                render_context.conformance_tests_running_context.current_testing_module_name,
                render_context.get_required_modules_functionalities(),
                previous_frid_code_diff,
                existing_conformance_test_files_content,
                render_context.conformance_tests_running_context.get_current_acceptance_tests(),
                previous_conformance_tests_issue,
                render_context.conformance_tests_running_context.fix_attempts,
                render_context.conformance_tests_running_context.get_current_conformance_test_folder_name(),
                render_context.conformance_tests_running_context.current_testing_frid_high_level_implementation_plan,
                run_state=render_context.run_state,
            )
        code_diff_files_content = {}

        if conformance_tests_fixed:
            render_context.conformance_tests.store_conformance_tests_files(
                render_context.module_name,
                render_context.required_modules,
                render_context.conformance_tests_running_context.current_testing_module_name,
                render_context.conformance_tests_running_context.get_current_conformance_test_folder_name(),
                response_files,
                existing_conformance_test_files,
            )
            code_diff_files_content = diff_utils.get_code_diff(response_files, existing_conformance_test_files_content)
            render_context.conformance_tests_running_context.code_diff_files = code_diff_files_content

            return self.IMPLEMENTATION_CODE_NOT_UPDATED, None
        else:
            if len(response_files) > 0:
                file_utils.store_response_files(render_context.build_folder, response_files, existing_files)
                code_diff_files_content = diff_utils.get_code_diff(response_files, existing_files_content)
                render_context.conformance_tests_running_context.code_diff_files = code_diff_files_content
                if render_context.verbose:
                    console.print_files(
                        "Files fixed:",
                        render_context.build_folder,
                        response_files,
                        style=console.OUTPUT_STYLE,
                    )
                render_context.conformance_tests_running_context.should_prepare_testing_environment = True
                return self.IMPLEMENTATION_CODE_UPDATED, None
            else:
                return self.IMPLEMENTATION_CODE_NOT_UPDATED, None
