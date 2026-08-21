from typing import Any

import git_utils
import plain_spec
from render_machine.actions.base_action import BaseAction
from render_machine.render_context import RenderContext


class CommitModuleConformanceTestsChanges(BaseAction):
    """Commit the module's implementation fixes and its conformance test suite."""

    SUCCESSFUL_OUTCOME_IMPLEMENTATION_UPDATED = "module_conformance_tests_committed_implementation_updated"
    SUCCESSFUL_OUTCOME_IMPLEMENTATION_NOT_UPDATED = "module_conformance_tests_committed_implementation_not_updated"

    def execute(self, render_context: RenderContext, _previous_action_payload: Any | None):
        ctx = render_context.module_conformance_tests_running_context

        implementation_updated = False
        if git_utils.is_dirty(render_context.build_folder):
            git_utils.add_all_files_and_commit(
                render_context.build_folder,
                git_utils.MODULE_CONFORMANCE_TESTS_FIXED_CODE_COMMIT_MESSAGE.format(render_context.module_name),
                render_context.module_name,
                plain_spec.MODULE_SCOPE_FRID,
                render_context.run_state.render_id,
            )
            implementation_updated = True

        conformance_tests_json = render_context.conformance_tests.get_conformance_tests_json(render_context.module_name)
        conformance_tests_json[plain_spec.MODULE_SCOPE_FRID] = {
            "folder_name": ctx.own_suite.folder_name,
            "conformance_tests_plan": ctx.conformance_tests_plan,
            "test_summary": ctx.test_summary,
            "uncovered_functionalities": ctx.uncovered_frids,
        }
        render_context.conformance_tests.dump_conformance_tests_json(render_context.module_name, conformance_tests_json)

        git_utils.add_all_files_and_commit(
            render_context.conformance_tests.get_module_conformance_tests_folder(render_context.module_name),
            git_utils.MODULE_CONFORMANCE_TESTS_PASSED_COMMIT_MESSAGE.format(render_context.module_name),
            render_context.module_name,
            None,
            render_context.run_state.render_id,
        )

        if implementation_updated:
            return self.SUCCESSFUL_OUTCOME_IMPLEMENTATION_UPDATED, None

        return self.SUCCESSFUL_OUTCOME_IMPLEMENTATION_NOT_UPDATED, None
