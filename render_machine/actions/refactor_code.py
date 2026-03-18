from typing import Any

import file_utils
from plain2code_console import console
from render_machine.actions.base_action import BaseAction
from render_machine.implementation_code_helpers import ImplementationCodeHelpers
from render_machine.render_context import RenderContext


class RefactorCode(BaseAction):
    SUCCESSFUL_OUTCOME = "refactoring_successful"
    NO_FILES_REFACTORED_OUTCOME = "no_files_refactored"

    def execute(self, render_context: RenderContext, _previous_action_payload: Any | None):
        existing_files, existing_files_content = ImplementationCodeHelpers.fetch_existing_files(
            render_context.build_folder
        )

        if render_context.verbose:
            console.info(f"Refactoring iteration {render_context.frid_context.refactoring_iteration}.")

        if render_context.verbose:
            console.print_files(
                "Files sent as input for refactoring:",
                render_context.build_folder,
                existing_files_content,
                style=console.INPUT_STYLE,
            )
        with console.status(
            f"[{console.INFO_STYLE}]Refactoring the generated code for functionality {render_context.frid_context.frid}..."
        ):
            response_files = render_context.codeplain_api.refactor_source_files_if_needed(
                frid=render_context.frid_context.frid,
                files_to_check=render_context.frid_context.changed_files,
                existing_files_content=existing_files_content,
                run_state=render_context.run_state,
            )

        if len(response_files) == 0:
            if render_context.verbose:
                console.info("No files refactored.")
            return self.NO_FILES_REFACTORED_OUTCOME, None

        file_utils.store_response_files(render_context.build_folder, response_files, existing_files)

        if render_context.verbose:
            console.print_files(
                "Files refactored:", render_context.build_folder, response_files, style=console.OUTPUT_STYLE
            )
        return self.SUCCESSFUL_OUTCOME, None
