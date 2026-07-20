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
from render_machine.render_types import RenderError, TestExecutionPhase

MAX_CONFORMANCE_TEST_FIX_ATTEMPTS = 20
MAX_CONFORMANCE_TEST_RERENDER_ATTEMPTS = 1


class FixConformanceTest(BaseAction):
    IMPLEMENTATION_CODE_NOT_UPDATED = "implementation_code_not_updated"
    IMPLEMENTATION_CODE_UPDATED = "implementation_code_updated"
    LIMIT_EXCEEDED_OUTCOME = "conformance_test_fix_limit_exceeded"
    REGENERATE_CONFORMANCE_TESTS_OUTCOME = "regenerate_conformance_tests"

    ISSUE_REASON_CODE_CONFORMANCE_TESTS = 0
    ISSUE_REASON_CODE_IMPLEMENTATION_CODE = 1
    ISSUE_REASON_CODE_CONFLICTING_REQUIREMENTS = 2
    ISSUE_REASON_CODE_CONFLICTING_ACCEPTANCE_TESTS = 3

    def execute(self, render_context: RenderContext, previous_action_payload: Any | None):
        ctx = render_context.conformance_tests_running_context
        ctx.fix_attempts += 1

        if ctx.fix_attempts >= MAX_CONFORMANCE_TEST_FIX_ATTEMPTS:
            if ctx.conformance_tests_render_attempts >= MAX_CONFORMANCE_TEST_RERENDER_ATTEMPTS:
                error_msg = f"The renderer was unable to produce an implementation that passes conformance tests for functionality '{render_context.frid_context.frid}' after many attempts. Please review and rewrite the specification. (Render ID: {render_context.run_state.render_id})"
                render_context.last_error_message = error_msg
                return (
                    self.LIMIT_EXCEEDED_OUTCOME,
                    RenderError.encode(message=error_msg).to_payload(),
                )
            else:
                ctx.regenerating_conformance_tests = True
                return self.REGENERATE_CONFORMANCE_TESTS_OUTCOME, None

        console.info(f"Running conformance tests attempt {ctx.fix_attempts + 1}.")

        console.info(
            f"Fixing conformance test for functionality {render_context.conformance_tests_running_context.current_testing_frid} in module {render_context.conformance_tests_running_context.current_testing_module_name}."
        )

        if not previous_action_payload.get("previous_conformance_tests_issue"):
            raise InternalClientError(
                "Internal client error: Previous action payload does not contain previous conformance tests issue."
            )
        previous_conformance_tests_issue = previous_action_payload["previous_conformance_tests_issue"]

        render_context.conformance_tests_running_context.previous_conformance_tests_issue_old = (
            previous_conformance_tests_issue
        )
        render_context.conformance_tests_running_context.previous_conformance_tests_issue_frid = (
            render_context.conformance_tests_running_context.current_testing_frid
        )
        render_context.conformance_tests_running_context.previous_conformance_tests_issue_module = (
            render_context.conformance_tests_running_context.current_testing_module_name
        )

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

        conflicting_module_name = render_context.conformance_tests_running_context.conflicting_module_name
        conflicting_frid = render_context.conformance_tests_running_context.conflicting_frid
        current_testing_module_name = render_context.conformance_tests_running_context.current_testing_module_name
        current_testing_frid = render_context.conformance_tests_running_context.current_testing_frid

        # Reset the conflicting requirement count if the current testing functionality is not the same as the previously conflicting functionality
        if conflicting_module_name != current_testing_module_name or conflicting_frid != current_testing_frid:
            render_context.conformance_tests_running_context.conflicting_requirement_count = 0

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

        [issue_reason_code, response_files] = render_context.codeplain_api.fix_conformance_tests_issue(
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
            render_context.conformance_tests_running_context.conflicting_requirement_count,
            run_state=render_context.run_state,
        )
        code_diff_files_content = {}

        if (
            issue_reason_code == self.ISSUE_REASON_CODE_CONFLICTING_REQUIREMENTS
            or issue_reason_code == self.ISSUE_REASON_CODE_CONFLICTING_ACCEPTANCE_TESTS
        ):
            render_context.conformance_tests_running_context.conflicting_requirement_count += 1
            render_context.conformance_tests_running_context.conflicting_module_name = current_testing_module_name
            render_context.conformance_tests_running_context.conflicting_frid = current_testing_frid
            console.info(
                f"[#FFB454]Potential conflicting functionalities detected while fixing conformance tests for functionality {current_testing_frid} in module {current_testing_module_name}.[/#FFB454]"
            )

        if issue_reason_code == self.ISSUE_REASON_CODE_CONFORMANCE_TESTS:
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
                console.print_files(
                    "Files fixed:",
                    render_context.build_folder,
                    response_files,
                    style=console.OUTPUT_STYLE,
                )
                render_context.conformance_tests_running_context.should_prepare_testing_environment = True

                # Record which test triggered the change and transition to retry phase
                ctx = render_context.conformance_tests_running_context
                ctx.test_that_triggered_code_change = (ctx.current_testing_module_name, ctx.current_testing_frid)
                ctx.execution_phase = TestExecutionPhase.RETRYING_AFTER_CODE_CHANGE

                return self.IMPLEMENTATION_CODE_UPDATED, None
            else:
                return self.IMPLEMENTATION_CODE_NOT_UPDATED, None
