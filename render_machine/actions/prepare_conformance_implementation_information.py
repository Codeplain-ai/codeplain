from typing import Any

import plain_spec
import render_machine.render_utils as render_utils
from memory_management import MemoryManager
from render_machine.actions.base_action import BaseAction
from render_machine.implementation_code_helpers import ImplementationCodeHelpers
from render_machine.render_context import RenderContext
from render_machine.render_types import RenderError


class PrepareConformanceImplementationInformation(BaseAction):
    SUCCESSFUL_OUTCOME = "conformance_implementation_information_prepared"
    FAILED_OUTCOME = "conformance_implementation_information_preparation_failed"

    def execute(self, render_context: RenderContext, _previous_action_payload: Any | None):
        # This action should only be called when prepare_conformance_test_fix_script is set
        # (checked by start_prepare_conformance_implementation_information in RenderContext)
        assert render_context.prepare_conformance_test_fix_script is not None
        # This action is only called during conformance test processing when current_testing_frid is set
        assert render_context.conformance_tests_running_context.current_testing_frid is not None

        _, existing_files_content = ImplementationCodeHelpers.fetch_existing_files(render_context.build_folder)
        _, memory_files_content = MemoryManager.fetch_memory_files(render_context.memory_manager.memory_folder)

        with open(render_context.prepare_conformance_test_fix_script, "r", encoding="utf-8") as f:
            prepare_conformance_implementation_script_content = f.read()

        # Check if conformance tests folder exists yet
        if render_context.conformance_tests_running_context.current_conformance_tests_exist():
            conformance_tests_folder_name = (
                render_context.conformance_tests_running_context.get_current_conformance_test_folder_name()
            )
        else:
            # Folder will be generated later by RenderConformanceTests action
            conformance_tests_folder_name = ""

        # Determine which acceptance tests to include based on phase
        # Phase 0 = rendering conformance tests (include all acceptance tests)
        # Phase > 0 = rendering specific acceptance test (include only that one)
        if render_context.conformance_tests_running_context.conformance_test_phase_index == 0:
            # Preparing for conformance tests rendering - include all acceptance tests
            all_acceptance_tests = render_context.frid_context.specifications.get(plain_spec.ACCEPTANCE_TESTS, [])
        else:
            # Preparing for acceptance test rendering - include only the current one
            acceptance_test = render_context.conformance_tests_running_context.get_current_acceptance_test()
            all_acceptance_tests = [acceptance_test] if acceptance_test else []

        api_response = render_context.codeplain_api.prepare_conformance_implementation(
            frid=render_context.frid_context.frid,
            functional_requirement_id=render_context.conformance_tests_running_context.current_testing_frid,
            plain_source_tree=render_context.plain_source_tree,
            linked_resources=render_context.frid_context.linked_resources,
            existing_files_content=existing_files_content,
            memory_files_content=memory_files_content,
            module_name=render_context.module_name,
            required_modules=render_context.get_required_modules_functionalities(),
            conformance_tests_folder_name=conformance_tests_folder_name,
            conformance_tests_json=render_context.conformance_tests_running_context.get_conformance_tests_json(
                render_context.conformance_tests_running_context.current_testing_module_name
            ),
            all_acceptance_tests=all_acceptance_tests,
            run_state=render_context.run_state,
            prepare_conformance_implementation_script=prepare_conformance_implementation_script_content,
        )
        instructions = api_response.get("instructions", "")

        exit_code, conformance_implementation_information, script_output_path = render_utils.execute_script(
            render_context.prepare_conformance_test_fix_script,
            [instructions],
            render_context.verbose,
            "Prepare Conformance Implementation Information",
            timeout=render_context.test_script_timeout,
            stop_event=render_context.stop_event,
        )

        if exit_code == 0 or exit_code == render_utils.TIMEOUT_ERROR_EXIT_CODE:
            render_context.conformance_tests_running_context.conformance_implementation_information = (
                conformance_implementation_information
            )
            render_context.script_execution_history.latest_prepare_conformance_implementation_output_path = (
                script_output_path
            )
            render_context.script_execution_history.should_update_script_outputs = True
            return self.SUCCESSFUL_OUTCOME, None

        return (
            self.FAILED_OUTCOME,
            RenderError.encode(
                message="Prepare conformance implementation information failed. Please check the prepare_conformance_test_fix_script.",
                error_type="PREPARE_CONFORMANCE_IMPLEMENTATION_ERROR",
                exit_code=exit_code,
                script=render_context.prepare_conformance_test_fix_script,
            ).to_payload(),
        )
