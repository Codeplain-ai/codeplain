import os
from typing import Any

import plain_spec
import render_machine.render_utils as render_utils
from plain2code_console import console
from render_machine.actions.base_action import BaseAction
from render_machine.render_context import RenderContext
from render_machine.render_types import RenderError

UNRECOVERABLE_ERROR_EXIT_CODES = [69]


class RunModuleConformanceTests(BaseAction):
    """Run the conformance test suite the module conformance phase is currently on.

    The phase runs the suites of the required modules first, as regression, and this module's own
    suite last. The action advances to the next suite itself, so a suite passing needs no guard on
    the transition.
    """

    MOVE_TO_NEXT_SUITE_OUTCOME = "module_conformance_suite_passed"
    ALL_SUITES_PASSED_OUTCOME = "all_module_conformance_tests_passed"
    FAILED_OUTCOME = "module_conformance_tests_failed"
    UNRECOVERABLE_ERROR_OUTCOME = "module_conformance_tests_unrecoverable_error_occurred"

    def execute(self, render_context: RenderContext, _previous_action_payload: Any | None):
        ctx = render_context.module_conformance_tests_running_context
        suite = ctx.current_suite

        if not suite.exists():
            # A required module that was rendered without conformance tests has nothing to regress.
            console.debug(f"Module {suite.module_name} has no conformance tests to run.")
            return self._proceed_to_next_suite(render_context)

        conformance_tests_script = os.path.normpath(render_context.conformance_tests_script)
        conformance_tests_folder_name = self._resolve_suite_folder_name(render_context, suite)

        console.info(
            f"Running conformance tests script {conformance_tests_script} "
            f"for {conformance_tests_folder_name} (module {suite.module_name})."
        )

        exit_code, conformance_tests_issue, conformance_tests_temp_log_file_path = render_utils.execute_script(
            conformance_tests_script,
            [render_context.build_folder, conformance_tests_folder_name],
            "Conformance Tests",
            frid=plain_spec.MODULE_SCOPE_FRID,
            module=suite.module_name,
            timeout=render_context.test_script_timeout,
            stop_event=render_context.stop_event,
        )
        render_context.script_execution_history.latest_conformance_test_output_path = (
            conformance_tests_temp_log_file_path
        )
        render_context.script_execution_history.should_update_script_outputs = True

        render_context.memory_manager.create_module_conformance_tests_memory(
            render_context, exit_code, conformance_tests_issue
        )

        if exit_code == 0:
            if suite.is_own_module:
                render_context.memory_manager.delete_unresolved_memory_files()
            return self._proceed_to_next_suite(render_context)

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

    def _resolve_suite_folder_name(self, render_context: RenderContext, suite) -> str:
        """Where the suite is run from.

        A required module's suite is run from this module's copy of it when one exists, the same way
        per-functionality regression does, so that fixes to it live with this module.
        """
        if suite.module_name == render_context.module_name:
            return suite.folder_name

        [source_conformance_test_folder_name, _] = (
            render_context.conformance_tests.get_source_conformance_test_folder_name(
                render_context.module_name,
                render_context.required_modules,
                suite.module_name,
                suite.folder_name,
            )
        )
        return source_conformance_test_folder_name

    def _proceed_to_next_suite(self, render_context: RenderContext):
        ctx = render_context.module_conformance_tests_running_context

        if ctx.has_next_suite():
            ctx.move_to_next_suite()
            ctx.fix_attempts = 0
            return self.MOVE_TO_NEXT_SUITE_OUTCOME, None

        return self.ALL_SUITES_PASSED_OUTCOME, None
