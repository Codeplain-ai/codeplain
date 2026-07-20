from typing import Any

import render_machine.render_utils as render_utils
from plain2code_console import console
from render_machine.actions.base_action import BaseAction
from render_machine.render_context import RenderContext
from render_machine.render_types import RenderError

UNRECOVERABLE_ERROR_EXIT_CODES = [69]


class PrepareTestingEnvironment(BaseAction):
    SUCCESSFUL_OUTCOME = "testing_environment_prepared"
    FAILED_OUTCOME = "testing_environment_preparation_failed"
    UNRECOVERABLE_ERROR_OUTCOME = "testing_environment_unrecoverable_error"

    def execute(self, render_context: RenderContext, _previous_action_payload: Any | None):
        if (
            render_context.prepare_environment_script is None
            or not render_context.conformance_tests_running_context.should_prepare_testing_environment
        ):
            return self.SUCCESSFUL_OUTCOME, None

        console.info(
            f"Running testing environment preparation script {render_context.prepare_environment_script} for build folder {render_context.build_folder}."
        )
        exit_code, preparation_issue, preparation_temp_file_path = render_utils.execute_script(
            render_context.prepare_environment_script,
            [render_context.build_folder],
            "Testing Environment Preparation",
            timeout=render_context.test_script_timeout,
            stop_event=render_context.stop_event,
        )

        render_context.script_execution_history.latest_testing_environment_output_path = preparation_temp_file_path
        render_context.script_execution_history.should_update_script_outputs = True

        if exit_code == 0:
            render_context.conformance_tests_running_context.should_prepare_testing_environment = False
            return self.SUCCESSFUL_OUTCOME, None

        if exit_code in UNRECOVERABLE_ERROR_EXIT_CODES:
            console.error(preparation_issue)
            return (
                self.UNRECOVERABLE_ERROR_OUTCOME,
                RenderError.encode(
                    message="Testing environment preparation failed due to problems in the environment setup. Please check your environment or update the preparation script.",
                    error_type="ENVIRONMENT_ERROR",
                    exit_code=exit_code,
                    script=render_context.prepare_environment_script,
                    issue=preparation_issue,
                ).to_payload(),
            )

        # A failed preparation (typically a build/compile error introduced by the
        # latest code changes) is handled like a failing conformance test: the fix
        # loop gets the preparation output and corrects the code. The flag stays
        # armed so the preparation re-runs after the fix is applied.
        error_message = "Testing environment preparation failed."
        if preparation_temp_file_path:
            error_message += f" Full output available at: {preparation_temp_file_path}"
        payload = RenderError.encode(
            message=error_message,
            error_type="ENVIRONMENT_ERROR",
            exit_code=exit_code,
            script=render_context.prepare_environment_script,
            output_file=preparation_temp_file_path,
        ).to_payload()
        # The legacy (non-agent) fixer requires the failure text under this key.
        payload["previous_conformance_tests_issue"] = preparation_issue
        return self.FAILED_OUTCOME, payload
