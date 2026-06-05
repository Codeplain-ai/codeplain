from typing import Any

import git_utils
from render_machine.actions.base_action import BaseAction
from render_machine.render_context import RenderContext


class FinishFunctionalRequirement(BaseAction):
    SUCCESSFUL_OUTCOME = "functional_requirement_finished"

    def execute(self, render_context: RenderContext, _previous_action_payload: Any | None):
        render_context.plain_module.update_frid_in_module_metadata(
            render_context.frid_context.frid,
            render_context.frid_context.functional_requirement_text,
        )

        commit_message = (
            git_utils.FUNCTIONAL_REQUIREMENT_REIMPLEMENTED_COMMIT_MESSAGE
            if render_context.is_rerender
            else git_utils.FUNCTIONAL_REQUIREMENT_FINISHED_COMMIT_MESSAGE
        )
        git_utils.add_all_files_and_commit(
            render_context.build_folder,
            commit_message.format(render_context.frid_context.frid),
            render_context.module_name,
            render_context.frid_context.frid,
            render_context.run_state.render_id,
        )

        render_context.codeplain_api.finish_functional_requirement(
            render_context.frid_context.frid,
            module_name=render_context.module_name,
            run_state=render_context.run_state,
        )

        return self.SUCCESSFUL_OUTCOME, None
