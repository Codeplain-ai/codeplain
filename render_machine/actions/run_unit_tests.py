import os
from typing import Any

import render_machine.render_utils as render_utils
from memory_management import Failure, Intervention, Scope, Suite, fingerprint_output
from plain2code_console import console
from render_machine.actions.base_action import BaseAction
from render_machine.render_context import RenderContext
from render_machine.render_types import RenderError

UNRECOVERABLE_ERROR_EXIT_CODES = [69]


class RunUnitTests(BaseAction):
    SUCCESSFUL_OUTCOME = "unit_tests_succeeded"
    FAILED_OUTCOME = "unit_tests_failed"
    UNRECOVERABLE_ERROR_OUTCOME = "unrecoverable_error_occurred"

    def execute(self, render_context: RenderContext, _previous_action_payload: Any | None):
        unittests_script = os.path.normpath(render_context.unittests_script)

        console.info(
            f"Running unit tests script {unittests_script}. (attempt: {render_context.unit_tests_running_context.fix_attempts + 1})"
        )
        exit_code, unittests_issue, unittests_temp_log_file_path = render_utils.execute_script(
            unittests_script,
            [render_context.build_folder],
            "Unit Tests",
            timeout=render_context.test_script_timeout,
            stop_event=render_context.stop_event,
        )

        render_context.script_execution_history.latest_unit_test_output_path = unittests_temp_log_file_path
        render_context.script_execution_history.should_update_script_outputs = True

        self._record_observation(render_context, exit_code, unittests_issue)

        if exit_code == 0:
            return self.SUCCESSFUL_OUTCOME, None

        elif exit_code in UNRECOVERABLE_ERROR_EXIT_CODES:
            console.error(unittests_issue)

            return (
                self.UNRECOVERABLE_ERROR_OUTCOME,
                RenderError.encode(
                    message="Unit tests script failed due to problems in the environment setup. Please check your environment or update the script for running unittests.",
                    error_type="ENVIRONMENT_ERROR",
                    script=unittests_script,
                    issue=unittests_issue,
                ).to_payload(),
            )
        else:
            return self.FAILED_OUTCOME, {"previous_unittests_issue": unittests_issue}

    @staticmethod
    def _record_observation(render_context: RenderContext, exit_code: int, unittests_issue: str) -> None:
        """Log what the fix applied before this run actually did.

        Deterministic - no API call, no credit. Nothing is recorded until a fix has been
        applied, because a record describes the effect of a change: the first failure of a
        functionality is context the render already has.
        """
        running_context = render_context.unit_tests_running_context
        if running_context is None or running_context.pending_intervention is None:
            return

        pending = running_context.pending_intervention
        running_context.pending_intervention = None

        fingerprint_before, signature_before, excerpt_before = fingerprint_output(pending.failure_output)
        fingerprint_after, _, _ = fingerprint_output(unittests_issue if exit_code != 0 else None)

        render_context.memory_store.record_observation(
            scope=Scope(
                module=render_context.module_name,
                frid=render_context.frid_context.frid,
                testing_module=render_context.module_name,
                testing_frid=render_context.frid_context.frid,
                suite=Suite.UNITTEST.value,
            ),
            failure=Failure(
                fingerprint=fingerprint_before,
                signature=signature_before,
                excerpt=excerpt_before,
                exit_code=1,
            ),
            intervention=Intervention(
                attempt_index=pending.attempt_index,
                target=pending.target,
                files_changed=sorted(pending.files_changed),
                lines_changed=pending.lines_changed,
            ),
            exit_code_after=exit_code,
            fingerprint_after=fingerprint_after,
            render_id=render_context.run_state.render_id,
        )
