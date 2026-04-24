from typing import Any

import render_machine.render_utils as render_utils
from memory_management import MemoryManager
from render_machine.actions.base_action import BaseAction
from render_machine.implementation_code_helpers import ImplementationCodeHelpers
from render_machine.render_context import RenderContext
from render_machine.render_types import RenderError


class PrepareImplementationInformation(BaseAction):
    SUCCESSFUL_OUTCOME = "implementation_information_prepared"
    FAILED_OUTCOME = "implementation_information_preparation_failed"

    def execute(self, render_context: RenderContext, _previous_action_payload: Any | None):
        _, existing_files_content = ImplementationCodeHelpers.fetch_existing_files(render_context.build_folder)
        _, memory_files_content = MemoryManager.fetch_memory_files(render_context.memory_manager.memory_folder)

        with open(render_context.prepare_implementation_script, "r", encoding="utf-8") as f:
            prepare_implementation_script_content = f.read()

        api_response = render_context.codeplain_api.prepare_implementation(
            frid=render_context.frid_context.frid,
            plain_source_tree=render_context.plain_source_tree,
            linked_resources=render_context.frid_context.linked_resources,
            existing_files_content=existing_files_content,
            memory_files_content=memory_files_content,
            module_name=render_context.module_name,
            required_modules=render_context.get_required_modules_functionalities(),
            include_unittests=render_context.should_run_unit_tests(),
            run_state=render_context.run_state,
            prepare_implementation_script=prepare_implementation_script_content,
        )
        instructions = api_response.get("instructions", "")

        exit_code, implementation_information, script_output_path = render_utils.execute_script(
            render_context.prepare_implementation_script,
            [instructions],
            render_context.verbose,
            "Prepare Implementation Information",
            timeout=render_context.test_script_timeout,
            stop_event=render_context.stop_event,
        )

        if exit_code == 0 or exit_code == render_utils.TIMEOUT_ERROR_EXIT_CODE:
            render_context.frid_context.implementation_information = implementation_information
            render_context.script_execution_history.latest_prepare_implementation_output_path = script_output_path
            render_context.script_execution_history.should_update_script_outputs = True
            return self.SUCCESSFUL_OUTCOME, None

        return (
            self.FAILED_OUTCOME,
            RenderError.encode(
                message="Prepare implementation information failed. Please check the prepare_implementation_script.",
                error_type="PREPARE_IMPLEMENTATION_ERROR",
                exit_code=exit_code,
                script=render_context.prepare_implementation_script,
            ).to_payload(),
        )
