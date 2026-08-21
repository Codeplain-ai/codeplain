import os
from typing import Any

import render_machine.render_utils as render_utils
from memory_management import Failure, Intervention, Scope, Suite, fingerprint_output
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

        self._record_observation(render_context, exit_code, conformance_tests_issue)

        if exit_code == 0:
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

    def _record_observation(self, render_context: RenderContext, exit_code: int, conformance_tests_issue: str) -> None:
        """Log what the intervention applied before this run actually did.

        Purely deterministic - no API call, no credit. There is nothing to record until an
        intervention has been applied, because a record describes the effect of a change:
        the very first failure of a functionality is context the render already has.
        """
        running_context = render_context.conformance_tests_running_context
        pending = running_context.pending_intervention
        if pending is None:
            return

        running_context.pending_intervention = None

        aimed_at_this_test = (
            pending.failure_testing_frid == running_context.current_testing_frid
            and pending.failure_testing_module == running_context.current_testing_module_name
        )
        if not aimed_at_this_test and exit_code == 0:
            # A test the intervention was not aimed at still passes. Nothing observed.
            return

        # When the run under observation is not the one the intervention targeted, there
        # was no failure here to compare against, so a failure now is a regression.
        target_output = pending.failure_output if aimed_at_this_test else None
        fingerprint_before, causes_before = fingerprint_output(target_output)
        fingerprint_after, _ = fingerprint_output(conformance_tests_issue if exit_code != 0 else None)

        render_context.memory_store.record_observation(
            scope=Scope(
                module=render_context.module_name,
                frid=render_context.frid_context.frid,
                testing_module=running_context.current_testing_module_name,
                testing_frid=running_context.current_testing_frid,
                suite=Suite.CONFORMANCE.value,
                test_name=running_context.get_current_conformance_test_folder_name(),
            ),
            failure=Failure(
                fingerprint=fingerprint_before,
                causes=causes_before,
                exit_code=1,
                output_path=pending.failure_output_path if aimed_at_this_test else None,
            ),
            intervention=Intervention(
                attempt_index=pending.attempt_index,
                target=pending.target,
                files_changed=sorted(pending.files_changed),
                lines_changed=pending.lines_changed,
                diff=pending.diff,
                touched_implementation=pending.touched_implementation,
                touched_test_files=pending.touched_test_files,
            ),
            exit_code_after=exit_code,
            fingerprint_after=fingerprint_after,
            render_id=render_context.run_state.render_id,
        )
