from typing import Any

import render_machine.render_utils as render_utils
from plain2code_console import console
from plain2code_events import RenderStateUpdated
from render_machine.actions.base_action import BaseAction
from render_machine.render_context import RenderContext
from render_machine.render_types import RenderError


class PrepareTestingEnvironment(BaseAction):
    SUCCESSFUL_OUTCOME = "testing_environment_prepared"
    FAILED_OUTCOME = "testing_environment_preparation_failed"

    def execute(self, render_context: RenderContext, _previous_action_payload: Any | None):
        if render_context.verbose:
            console.info(
                f"Running testing environment preparation script {render_context.prepare_environment_script} for build folder {render_context.build_folder}."
            )
        exit_code, _, preparation_temp_file_path = render_utils.execute_script(
            render_context.prepare_environment_script,
            [render_context.build_folder],
            render_context.verbose,
            "Testing Environment Preparation",
        )

        render_context.conformance_tests_running_context.should_prepare_testing_environment = False
        render_context.script_execution_history.latest_testing_environment_output_path = preparation_temp_file_path
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
        else:
            return (
                self.FAILED_OUTCOME,
                RenderError.encode(
                    message="Testing environment preparation failed. Please check the preparation script.",
                    error_type="ENVIRONMENT_ERROR",
                    exit_code=exit_code,
                    script=render_context.prepare_environment_script,
                ).to_payload(),
            )
