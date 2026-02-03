from typing import Any

from plain2code_console import console
from render_machine.actions.base_action import BaseAction
from render_machine.render_context import RenderContext


class ExitWithError(BaseAction):
    SUCCESSFUL_OUTCOME = "error_handled"

    def execute(self, render_context: RenderContext, previous_action_payload: Any | None):
        console.error(previous_action_payload)

        render_context.codeplain_api.fail_functional_requirement(
            render_context.frid_context.frid,
            run_state=render_context.run_state,
        )

        if render_context.frid_context is not None:
            console.info(
                f"To continue rendering from the last successfully rendered functional requirement, provide the [red]--render-from {render_context.frid_context.frid}[/red] flag."
            )

        if render_context.run_state.render_id is not None:
            console.info(f"Render ID: {render_context.run_state.render_id}")

        return self.SUCCESSFUL_OUTCOME, previous_action_payload
