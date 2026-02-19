from typing import Any

import render_machine.render_utils as render_utils
from plain2code_console import console
from plain2code_events import RenderStateUpdated
from render_machine.actions.base_action import BaseAction
from render_machine.render_context import RenderContext
from render_machine.render_types import RenderError

UNRECOVERABLE_ERROR_EXIT_CODES = [69]


class RunUnitTests(BaseAction):
    SUCCESSFUL_OUTCOME = "unit_tests_succeeded"
    FAILED_OUTCOME = "unit_tests_failed"
    UNRECOVERABLE_ERROR_OUTCOME = "unrecoverable_error_occurred"

    def execute(self, render_context: RenderContext, _previous_action_payload: Any | None):
        if render_context.verbose:
            console.info(
                f"Running unit tests script {render_context.unittests_script}. (attempt: {render_context.unit_tests_running_context.fix_attempts + 1})"
            )
        exit_code, unittests_issue, unittests_temp_file_path = render_utils.execute_script(
            render_context.unittests_script,
            [render_context.build_folder],
            render_context.verbose,
            "Unit Tests",
        )

        render_context.script_execution_history.latest_unit_test_output_path = unittests_temp_file_path
        render_context.script_execution_history.should_update_script_outputs = True
        render_context.event_bus.publish(
            RenderStateUpdated(
                state=render_context.state,
                previous_state=render_context.previous_state,
                snapshot=render_context.create_snapshot(),
            )
        )
        if exit_code == 0:
            return self.SUCCESSFUL_OUTCOME, None

        elif exit_code in UNRECOVERABLE_ERROR_EXIT_CODES:
            console.error(f"[red]{unittests_issue}[/red]")
            return (
                self.UNRECOVERABLE_ERROR_OUTCOME,
                RenderError.encode(
                    message="Unit tests script failed with an unrecoverable error.\nPlease check your unit test script output for more details.",
                    error_type="ENVIRONMENT_ERROR",
                    exit_code=exit_code,
                    script=render_context.unittests_script,
                ).to_payload(),
            )
        else:
            return self.FAILED_OUTCOME, {"previous_unittests_issue": unittests_issue}
