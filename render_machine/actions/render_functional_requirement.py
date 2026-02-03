from typing import Any

import file_utils
import render_machine.render_utils as render_utils
from memory_management import MemoryManager
from plain2code_console import console
from plain2code_exceptions import FunctionalRequirementTooComplex
from render_machine.actions.base_action import BaseAction
from render_machine.implementation_code_helpers import ImplementationCodeHelpers
from render_machine.render_context import RenderContext
from render_machine.render_types import RenderError

MAX_CODE_GENERATION_RETRIES = 2


class RenderFunctionalRequirement(BaseAction):
    SUCCESSFUL_OUTCOME = "code_and_unit_tests_generated"
    FUNCTIONAL_REQUIREMENT_TOO_COMPLEX_OUTCOME = "functional_requirement_too_complex"

    def execute(self, render_context: RenderContext, _previous_action_payload: Any | None):
        render_utils.revert_changes_for_frid(render_context)
        existing_files, existing_files_content = ImplementationCodeHelpers.fetch_existing_files(
            render_context.build_folder
        )
        _, memory_files_content = MemoryManager.fetch_memory_files(render_context.memory_manager.memory_folder)

        if render_context.verbose:
            msg = "-------------------------------------\n"
            msg += f"Module: {render_context.module_name}\n"
            msg += f"Rendering functionality {render_context.frid_context.frid}"
            if render_context.frid_context.functional_requirement_render_attempts > 1:
                msg += f", attempt number {render_context.frid_context.functional_requirement_render_attempts}/{MAX_CODE_GENERATION_RETRIES}."
            msg += f"\n{render_context.frid_context.functional_requirement_text}\n"
            msg += "-------------------------------------"
            console.info(msg)

        try:
            if render_context.verbose:
                render_utils.print_inputs(
                    render_context, existing_files_content, "Files sent as input to code generation:"
                )

            with console.status(
                f"[{console.INFO_STYLE}]Generating functional requirement {render_context.frid_context.frid}...\n"
            ):
                response_files = render_context.codeplain_api.render_functional_requirement(
                    render_context.frid_context.frid,
                    render_context.plain_source_tree,
                    render_context.frid_context.linked_resources,
                    existing_files_content,
                    memory_files_content,
                    render_context.module_name,
                    render_context.get_required_modules_functionalities(),
                    render_context.run_state,
                )
        except FunctionalRequirementTooComplex as e:
            error_message = f"The functional requirement:\n{render_context.frid_context.functional_requirement_text}\n is too complex to be implemented. Please break down the functional requirement into smaller parts ({str(e)})."
            if e.proposed_breakdown:
                error_message += "\nProposed breakdown:"
                for _, part in e.proposed_breakdown["functional_requirements"].items():
                    error_message += f"\n  - {part}"

            return (
                self.FUNCTIONAL_REQUIREMENT_TOO_COMPLEX_OUTCOME,
                RenderError.encode(
                    message=error_message,
                    error_type="COMPLEXITY_ERROR",
                    frid=render_context.frid_context.frid,
                    proposed_breakdown=e.proposed_breakdown,
                ).to_payload(),
            )

        _, changed_files = file_utils.update_build_folder_with_rendered_files(
            render_context.build_folder, existing_files, response_files
        )
        render_context.frid_context.changed_files.update(changed_files)

        if render_context.verbose:
            console.print_files(
                "Files generated or updated:",
                render_context.build_folder,
                response_files,
                style=console.OUTPUT_STYLE,
            )

        return self.SUCCESSFUL_OUTCOME, None
