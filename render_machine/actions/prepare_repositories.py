from typing import Any

import file_utils
import git_utils
import plain_spec
from plain2code_console import console
from render_machine.actions.base_action import BaseAction
from render_machine.render_context import RenderContext


class PrepareRepositories(BaseAction):
    SUCCESSFUL_OUTCOME = "repositories_prepared"

    def execute(self, render_context: RenderContext, _previous_action_payload: Any | None):
        if render_context.render_range is not None and render_context.render_range[0] != plain_spec.get_first_frid(
            render_context.plain_source_tree
        ):
            frid = render_context.render_range[0]

            render_context.starting_frid = frid

            previous_frid = plain_spec.get_previous_frid(render_context.plain_source_tree, frid)

            if render_context.verbose:
                console.debug(f"Reverting code to version implemented for {previous_frid}.")

            git_utils.revert_to_commit_with_frid(render_context.build_folder, previous_frid)
            # conformance tests are still not fully implemented
            if render_context.render_conformance_tests:
                git_utils.revert_to_commit_with_frid(
                    render_context.conformance_tests.get_module_conformance_tests_folder(render_context.module_name),
                    previous_frid,
                )

        else:
            if render_context.required_modules:
                previous_module = render_context.required_modules[-1]
                if render_context.verbose:
                    console.debug(f"Cloning git repo from module {previous_module.module_name}.")

                file_utils.delete_folder(render_context.build_folder)
                git_utils.clone_repo(
                    previous_module.module_build_folder,
                    render_context.build_folder,
                    render_context.module_name,
                    render_context.run_state.render_id,
                )
            else:
                if render_context.verbose:
                    console.debug("Initializing git repositories for the render folders.")

                git_utils.init_git_repo(
                    render_context.build_folder, render_context.module_name, render_context.run_state.render_id
                )

                if render_context.base_folder:
                    file_utils.copy_folder_content(render_context.base_folder, render_context.build_folder)
                    git_utils.add_all_files_and_commit(
                        render_context.build_folder,
                        git_utils.BASE_FOLDER_COMMIT_MESSAGE,
                        render_context.module_name,
                        None,
                        render_context.run_state.render_id,
                    )

            if render_context.render_conformance_tests:
                git_utils.init_git_repo(
                    render_context.conformance_tests.get_module_conformance_tests_folder(render_context.module_name),
                    render_context.module_name,
                    render_context.run_state.render_id,
                )

        return self.SUCCESSFUL_OUTCOME, None
