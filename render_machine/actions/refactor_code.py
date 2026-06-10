from typing import Any

import file_utils
from plain2code_console import console
from render_machine.actions.base_action import BaseAction
from render_machine.implementation_code_helpers import ImplementationCodeHelpers
from render_machine.render_context import RenderContext


MAX_REFACTORING_ITERATIONS = 5


class RefactorCode(BaseAction):
    SUCCESSFUL_OUTCOME = "refactoring_successful"
    NO_FILES_REFACTORED_OUTCOME = "no_files_refactored"
    ITERATION_LIMIT_EXCEEDED_OUTCOME = "refactoring_iteration_limit_exceeded"

    def execute(self, render_context: RenderContext, _previous_action_payload: Any | None):
        if render_context.frid_context.refactoring_iteration == 0:
            console.info("Refactoring the generated code...")

        render_context.frid_context.refactoring_iteration += 1

        if render_context.frid_context.refactoring_iteration >= MAX_REFACTORING_ITERATIONS:
            console.info(
                f"Refactoring iterations limit of {MAX_REFACTORING_ITERATIONS} reached for functionality {render_context.frid_context.frid}."
            )
            return self.ITERATION_LIMIT_EXCEEDED_OUTCOME, None

        existing_files, existing_files_content = ImplementationCodeHelpers.fetch_existing_files(
            render_context.build_folder
        )

        console.debug(f"Refactoring iteration {render_context.frid_context.refactoring_iteration}.")

        console.print_files(
            "Files sent as input for refactoring:",
            render_context.build_folder,
            existing_files_content,
            style=console.INPUT_STYLE,
        )

        response_files = render_context.codeplain_api.refactor_source_files_if_needed(
            frid=render_context.frid_context.frid,
            module_name=render_context.module_name,
            files_to_check=render_context.frid_context.changed_files,
            existing_files_content=existing_files_content,
            run_state=render_context.run_state,
        )

        if len(response_files) == 0:
            console.debug("No files refactored.")
            return self.NO_FILES_REFACTORED_OUTCOME, None

        file_utils.store_response_files(render_context.build_folder, response_files, existing_files)

        console.print_files(
            "Files refactored:", render_context.build_folder, response_files, style=console.OUTPUT_STYLE
        )
        return self.SUCCESSFUL_OUTCOME, None
