import os
from typing import Any

import conformance_test_journal
import failure_signature
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

    @staticmethod
    def _fingerprint_run(render_context: RenderContext, exit_code: int, conformance_tests_issue: str) -> None:
        """Fingerprint this run so the fix it triggers can be journalled against the failure that caused it.

        Every run is added to the project's line profile, passing runs especially: what separates a failure
        from the surrounding build chatter is that the chatter also shows up when the tests pass.
        """
        ctx = render_context.conformance_tests_running_context
        memory_folder = render_context.memory_manager.memory_folder

        profile = failure_signature.LineFrequencyProfile.load(memory_folder)
        profile.observe(
            conformance_tests_issue,
            passed=exit_code == 0,
            functionality=f"{ctx.current_testing_module_name}:{ctx.current_testing_frid}",
        )
        profile.save(memory_folder)

        if exit_code == 0:
            ctx.last_failure_signature = None
            ctx.last_failure_excerpt = None
            return

        ctx.last_failure_signature = failure_signature.compute_signature(conformance_tests_issue, exit_code, profile)
        ctx.last_failure_excerpt = conformance_test_journal.build_issue_excerpt(conformance_tests_issue)

    def execute(self, render_context: RenderContext, _previous_action_payload: Any | None):
        conformance_tests_script = os.path.normpath(render_context.conformance_tests_script)

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

        console.info(
            f"Running conformance tests script {conformance_tests_script} "
            + f"for {conformance_tests_folder_name} ("
            + f"functionality {render_context.conformance_tests_running_context.current_testing_frid} "
            + f"in module {render_context.conformance_tests_running_context.current_testing_module_name}"
            + ")."
        )
        exit_code, conformance_tests_issue, conformance_tests_temp_log_file_path = render_utils.execute_script(
            conformance_tests_script,
            [render_context.build_folder, conformance_tests_folder_name],
            "Conformance Tests",
            frid=render_context.conformance_tests_running_context.current_testing_frid,
            module=render_context.conformance_tests_running_context.current_testing_module_name,
            timeout=render_context.test_script_timeout,
            stop_event=render_context.stop_event,
        )
        render_context.script_execution_history.latest_conformance_test_output_path = (
            conformance_tests_temp_log_file_path
        )
        render_context.script_execution_history.should_update_script_outputs = True

        self._fingerprint_run(render_context, exit_code, conformance_tests_issue)

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
                    message="Conformance tests script failed due to problems in the environment setup. Please check your environment or update the script for running conformance tests.",
                    error_type="ENVIRONMENT_ERROR",
                    script=conformance_tests_script,
                    issue=conformance_tests_issue,
                ).to_payload(),
            )

        return self.FAILED_OUTCOME, {"previous_conformance_tests_issue": conformance_tests_issue}
