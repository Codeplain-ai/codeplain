from typing import Any

import render_machine.render_utils as render_utils
from plain2code_console import console
from render_machine.actions.base_action import BaseAction
from render_machine.render_context import RenderContext
from render_machine.render_types import RenderError

UNRECOVERABLE_ERROR_EXIT_CODES = [69]


class RunConformanceTests(BaseAction):

    SUCCESSFUL_OUTCOME = "conformance_tests_passed"
    FAILED_OUTCOME = "conformance_tests_failed"
    UNRECOVERABLE_ERROR_OUTCOME = "unrecoverable_error_occurred"

    def execute(self, render_context: RenderContext, _previous_action_payload: Any | None):
        if render_context.module_name == render_context.conformance_tests_running_context.current_testing_module_name:
            conformance_tests_folder_name = (
                render_context.conformance_tests_running_context.get_current_conformance_test_folder_name()
            )
        else:
            [conformance_tests_folder_name, _] = (
                render_context.conformance_tests.get_source_conformance_test_folder_name(
                    render_context.module_name,
                    render_context.required_modules,
                    render_context.conformance_tests_running_context.current_testing_module_name,
                    render_context.conformance_tests_running_context.get_current_conformance_test_folder_name(),
                )
            )

        if render_context.verbose:
            console.info(
                f"Running conformance tests script {render_context.conformance_tests_script} "
                + f"for {conformance_tests_folder_name} ("
                + f"functionality {render_context.conformance_tests_running_context.current_testing_frid} "
                + f"in module {render_context.conformance_tests_running_context.current_testing_module_name}"
                + ")."
            )
        exit_code, conformance_tests_issue, conformance_tests_temp_file_path = render_utils.execute_script(
            render_context.conformance_tests_script,
            [render_context.build_folder, conformance_tests_folder_name],
            render_context.verbose,
            "Conformance Tests",
            frid=render_context.conformance_tests_running_context.current_testing_frid,
            timeout=render_context.test_script_timeout,
            stop_event=render_context.stop_event,
        )
        render_context.script_execution_history.latest_conformance_test_output_path = conformance_tests_temp_file_path
        render_context.script_execution_history.should_update_script_outputs = True

        render_context.memory_manager.create_conformance_tests_memory(
            render_context, exit_code, conformance_tests_issue
        )

        if exit_code == 0:
            if (
                render_context.conformance_tests_running_context.current_testing_module_name
                == render_context.module_name
                and render_context.conformance_tests_running_context.current_testing_frid
                == render_context.frid_context.frid
            ):
                render_context.memory_manager.delete_unresolved_memory_files()
            return self.SUCCESSFUL_OUTCOME, None

        if exit_code in UNRECOVERABLE_ERROR_EXIT_CODES:
            console.error(conformance_tests_issue)
            return (
                self.UNRECOVERABLE_ERROR_OUTCOME,
                RenderError.encode(
                    message=conformance_tests_issue,
                    error_type="ENVIRONMENT_ERROR",
                    exit_code=exit_code,
                    script=render_context.conformance_tests_script,
                    frid=render_context.conformance_tests_running_context.current_testing_frid,
                ).to_payload(),
            )

        return self.FAILED_OUTCOME, {"previous_conformance_tests_issue": conformance_tests_issue}
