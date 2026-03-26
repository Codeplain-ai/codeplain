from typing import Any

import file_utils
from plain2code_console import console
from render_machine.actions.base_action import BaseAction
from render_machine.render_context import RenderContext


class CreateDist(BaseAction):
    SUCCESSFUL_OUTCOME = "dist_created"

    def execute(self, render_context: RenderContext, _previous_action_payload: Any | None):
        # Copy build and conformance tests folders to output folders if specified
        if render_context.copy_build:
            file_utils.copy_folder_to_output(
                render_context.build_folder,
                render_context.build_dest,
            )
        if render_context.copy_conformance_tests:
            file_utils.copy_folder_to_output(
                render_context.conformance_tests.get_module_conformance_tests_folder(render_context.module_name),
                render_context.conformance_tests_dest,
            )
        console.info(f"[#79FC96]Render of module {render_context.module_name} completed successfully.[/#79FC96]")

        return self.SUCCESSFUL_OUTCOME, None
