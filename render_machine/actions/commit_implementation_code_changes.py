from typing import Any

import git_utils
import repo_map
from render_machine.actions.base_action import BaseAction
from render_machine.render_context import RenderContext


class CommitImplementationCodeChanges(BaseAction):
    SUCCESSFUL_OUTCOME = "implementation_code_changes_committed"

    def __init__(self, base_commit_message: str):
        self.base_commit_message = base_commit_message

    def execute(self, render_context: RenderContext, _previous_action_payload: Any | None):
        # Record this FRID in the module's rolling code brief before committing, so the
        # brief travels with the same commit and future agent sessions see it.
        repo_map.append_code_brief_entry(
            render_context.build_folder,
            render_context.frid_context.frid,
            render_context.frid_context.functional_requirement_text,
            git_utils.get_dirty_file_names(render_context.build_folder),
        )

        git_utils.add_all_files_and_commit(
            render_context.build_folder,
            self.base_commit_message.format(render_context.frid_context.frid),
            render_context.module_name,
            render_context.frid_context.frid,
            render_context.run_state.render_id,
        )

        return self.SUCCESSFUL_OUTCOME, None
