from typing import Any

import git_utils
from render_machine.actions.base_action import BaseAction
from render_machine.render_context import RenderContext


class FinishModuleConformanceTests(BaseAction):
    """Close the module conformance testing phase.

    Marks the module as fully implemented in git and in the module metadata, and debits the credit
    the phase was reserved against.
    """

    SUCCESSFUL_OUTCOME = "module_conformance_tests_finished"

    def execute(self, render_context: RenderContext, _previous_action_payload: Any | None):
        git_utils.add_all_files_and_commit(
            render_context.build_folder,
            git_utils.MODULE_FULLY_IMPLEMENTED_COMMIT_MESSAGE.format(render_context.module_name),
            render_context.module_name,
            None,
            render_context.run_state.render_id,
        )

        render_context.codeplain_api.finish_module_conformance_tests(
            module_name=render_context.module_name,
            run_state=render_context.run_state,
        )

        return self.SUCCESSFUL_OUTCOME, None
