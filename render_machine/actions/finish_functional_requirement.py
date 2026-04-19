from typing import Any

from render_machine.actions.commit_implementation_code_changes import CommitImplementationCodeChanges
from render_machine.render_context import RenderContext


class FinishFunctionalRequirement(CommitImplementationCodeChanges):
    SUCCESSFUL_OUTCOME = "functional_requirement_finished"

    def execute(self, render_context: RenderContext, previous_action_payload: Any | None):
        super().execute(render_context, previous_action_payload)

        render_context.codeplain_api.finish_functional_requirement(
            render_context.frid_context.frid,
            module_name=render_context.module_name,
            run_state=render_context.run_state,
        )

        return self.SUCCESSFUL_OUTCOME, None
