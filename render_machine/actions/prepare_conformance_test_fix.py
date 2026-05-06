from typing import Any

import render_machine.render_utils as render_utils
from memory_management import MemoryManager
from plain2code_console import console
from plain2code_exceptions import InternalClientError
from render_machine.actions.base_action import BaseAction
from render_machine.implementation_code_helpers import ImplementationCodeHelpers
from render_machine.render_context import RenderContext
from render_machine.render_types import RenderError


class PrepareConformanceTestFix(BaseAction):
    SUCCESSFUL_OUTCOME = "conformance_test_fix_prepared"
    FAILED_OUTCOME = "conformance_test_fix_preparation_failed"

    def execute(self, render_context: RenderContext, previous_action_payload: Any | None):
        if render_context.verbose:
            console.info(
                f"Running prepare_conformance_test_fix_script for FRID "
                f"{render_context.conformance_tests_running_context.current_testing_frid}."
            )

        if not previous_action_payload or not previous_action_payload.get("previous_conformance_tests_issue"):
            raise InternalClientError("Previous action payload does not contain previous conformance tests issue.")
        previous_conformance_tests_issue = previous_action_payload["previous_conformance_tests_issue"]

        _, existing_files_content = ImplementationCodeHelpers.fetch_existing_files(render_context.build_folder)
        _, memory_files_content = MemoryManager.fetch_memory_files(render_context.memory_manager.memory_folder)
        (
            _,
            existing_conformance_test_files_content,
        ) = render_context.conformance_tests.fetch_existing_conformance_test_files(
            render_context.module_name,
            render_context.required_modules,
            render_context.conformance_tests_running_context.current_testing_module_name,
            render_context.conformance_tests_running_context.get_current_conformance_test_folder_name(),
        )
        previous_frid_code_diff = ImplementationCodeHelpers.get_code_diff(
            render_context.build_folder, render_context.plain_source_tree, render_context.frid_context.frid
        )

        with open(render_context.prepare_conformance_test_fix_script, "r", encoding="utf-8") as f:
            script_content = f.read()

        api_response = render_context.codeplain_api.prepare_conformance_test_fix(
            frid=render_context.frid_context.frid,
            functional_requirement_id=render_context.conformance_tests_running_context.current_testing_frid,
            plain_source_tree=render_context.plain_source_tree,
            linked_resources=render_context.frid_context.linked_resources,
            existing_files_content=existing_files_content,
            memory_files_content=memory_files_content,
            module_name=render_context.module_name,
            conformance_tests_module_name=(
                render_context.conformance_tests_running_context.current_testing_module_name
            ),
            required_modules=render_context.get_required_modules_functionalities(),
            code_diff=previous_frid_code_diff,
            conformance_tests_files=existing_conformance_test_files_content,
            acceptance_tests=render_context.conformance_tests_running_context.get_current_acceptance_tests(),
            conformance_tests_issue=previous_conformance_tests_issue,
            implementation_fix_count=render_context.conformance_tests_running_context.fix_attempts,
            conformance_tests_folder_name=(
                render_context.conformance_tests_running_context.get_current_conformance_test_folder_name()
            ),
            current_testing_frid_high_level_implementation_plan=(
                render_context.conformance_tests_running_context.current_testing_frid_high_level_implementation_plan
            ),
            run_state=render_context.run_state,
            prepare_conformance_test_fix_script=script_content,
        )

        instructions = api_response.get("instructions", "")

        exit_code, conformance_test_fix_information, script_output_path = render_utils.execute_script(
            render_context.prepare_conformance_test_fix_script,
            [instructions],
            render_context.verbose,
            "Prepare Conformance Test Fix",
            timeout=render_context.test_script_timeout,
            stop_event=render_context.stop_event,
        )

        if exit_code == 0 or exit_code == render_utils.TIMEOUT_ERROR_EXIT_CODE:
            render_context.conformance_tests_running_context.conformance_test_fix_information = (
                conformance_test_fix_information
            )
            render_context.script_execution_history.latest_prepare_conformance_test_fix_output_path = script_output_path
            render_context.script_execution_history.should_update_script_outputs = True
            return (
                self.SUCCESSFUL_OUTCOME,
                {"previous_conformance_tests_issue": previous_conformance_tests_issue},
            )

        return (
            self.FAILED_OUTCOME,
            RenderError.encode(
                message="Prepare conformance test fix failed. Please check the prepare_conformance_test_fix_script.",
                error_type="PREPARE_CONFORMANCE_TEST_FIX_ERROR",
                exit_code=exit_code,
                script=render_context.prepare_conformance_test_fix_script,
            ).to_payload(),
        )
