from typing import Any

import render_machine.render_utils as render_utils
from plain2code_console import console
from render_machine.actions.base_action import BaseAction
from render_machine.render_context import RenderContext
from render_machine.render_types import RenderError


class PrepareModuleTestingEnvironment(BaseAction):
    """Prepare the testing environment before the module's conformance tests are run.

    Runs at most once per state of the code: the module conformance phase clears the flag after
    preparing, and sets it again whenever the suite or the implementation code changes.
    """

    SUCCESSFUL_OUTCOME = "module_testing_environment_prepared"
    FAILED_OUTCOME = "module_testing_environment_preparation_failed"

    def execute(self, render_context: RenderContext, _previous_action_payload: Any | None):
        ctx = render_context.module_conformance_tests_running_context

        if render_context.prepare_environment_script is None or not ctx.should_prepare_testing_environment:
            return self.SUCCESSFUL_OUTCOME, None

        console.info(
            f"Running testing environment preparation script {render_context.prepare_environment_script} "
            f"for build folder {render_context.build_folder}."
        )
        exit_code, _, preparation_temp_file_path = render_utils.execute_script(
            render_context.prepare_environment_script,
            [render_context.build_folder],
            "Testing Environment Preparation",
            timeout=render_context.test_script_timeout,
            stop_event=render_context.stop_event,
        )

        ctx.should_prepare_testing_environment = False
        render_context.script_execution_history.latest_testing_environment_output_path = preparation_temp_file_path
        render_context.script_execution_history.should_update_script_outputs = True

        if exit_code == 0:
            return self.SUCCESSFUL_OUTCOME, None

        return (
            self.FAILED_OUTCOME,
            RenderError.encode(
                message="Testing environment preparation failed. Please check the preparation script.",
                error_type="ENVIRONMENT_ERROR",
                exit_code=exit_code,
                script=render_context.prepare_environment_script,
            ).to_payload(),
        )
