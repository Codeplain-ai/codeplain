from typing import Any

from plain2code_console import console
from render_machine.actions.base_action import BaseAction
from render_machine.render_context import RenderContext
from render_machine.render_types import RenderError


class ExitWithError(BaseAction):
    SUCCESSFUL_OUTCOME = "error_handled"

    def execute(self, render_context: RenderContext, previous_action_payload: Any | None):
        console.error(self._error_message(render_context, previous_action_payload))

        render_context.codeplain_api.fail_functional_requirement(
            render_context.frid_context.frid,
            module_name=render_context.module_name,
            run_state=render_context.run_state,
        )

        if render_context.frid_context is not None:
            console.info(
                f"To continue rendering from the last successfully rendered functionality, "
                f"provide the --render-from {render_context.frid_context.frid} flag."
            )

        if render_context.run_state.render_id is not None:
            console.info(f"Render ID: {render_context.run_state.render_id}")

        return (
            self.SUCCESSFUL_OUTCOME,
            RenderError.encode(
                message=render_context.last_error_message or "Unknown error",
            ).to_payload(),
        )

    @staticmethod
    def _error_message(render_context: RenderContext, previous_action_payload: Any | None) -> str:
        """What the user is told the render stopped for.

        Actions reach this state by three routes: some hand over an encoded RenderError
        payload, some a plain string, and some nothing at all. Printing the payload as it
        arrives showed a raw dict for the first and the word "None" for the last, so the
        reason is unwrapped here and falls back to the same message the returned payload
        carries.
        """
        if isinstance(previous_action_payload, dict):
            error = previous_action_payload.get("error")
            if isinstance(error, dict) and error.get("message"):
                return error["message"]
        elif previous_action_payload:
            return str(previous_action_payload)

        return render_context.last_error_message or "Unknown error"
