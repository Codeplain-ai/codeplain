import os
from typing import Any

import render_machine.render_utils as render_utils
from plain2code_console import console
from render_machine import agent_tools
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

        position = render_context.testing_functionality_position()
        console.info(
            f"Running conformance tests script {conformance_tests_script} "
            + f"for {conformance_tests_folder_name} ("
            + f"functionality {render_context.testing_functionality_label()}"
            + ")."
        )
        verified, verified_log_path = self._verified_passing_run(render_context, conformance_tests_folder_name)
        if verified:
            console.info("The conformance tests already passed in the agent's last run; not running them again.")
            exit_code, conformance_tests_issue, conformance_tests_temp_log_file_path = 0, "", verified_log_path
        else:
            exit_code, conformance_tests_issue, conformance_tests_temp_log_file_path = render_utils.execute_script(
                conformance_tests_script,
                [render_context.build_folder, conformance_tests_folder_name],
                "Conformance Tests",
                frid=render_context.conformance_tests_running_context.current_testing_frid,
                module=render_context.conformance_tests_running_context.current_testing_module_name,
                frid_position=position,
                timeout=render_context.test_script_timeout,
                stop_event=render_context.stop_event,
            )
        render_context.script_execution_history.latest_conformance_test_output_path = (
            conformance_tests_temp_log_file_path
        )
        render_context.script_execution_history.should_update_script_outputs = True

        if exit_code == 0:
            ctx = render_context.conformance_tests_running_context
            agents = ctx.conformance_fix_agents
            if agents is not None and agents.tested == (ctx.current_testing_module_name, str(ctx.current_testing_frid)):
                # The fix is approved and the tests pass: the fixer and the reviewer are done.
                ctx.conformance_fix_agents = None
            render_context.memory_manager.journal.record_result(
                render_context.conformance_tests_running_context.current_testing_module_name,
                render_context.conformance_tests_running_context.current_testing_frid,
                passed=True,
            )
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

    @staticmethod
    def _verified_passing_run(render_context: RenderContext, tests_folder: str) -> tuple[bool, str | None]:
        """Whether the conformance tests writer's own run passed on the files as they are now, and its log
        (consumed once)."""
        ctx = render_context.conformance_tests_running_context
        verified = getattr(ctx, "verified_passing_run", None)
        if verified is None:
            return False, None
        ctx.verified_passing_run = None
        files, log_path = verified
        if files != agent_tools.test_run_files(render_context, tests_folder):
            return False, None
        return True, log_path
