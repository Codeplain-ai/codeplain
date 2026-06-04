import json
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
        if render_context.is_rerender:
            return self._prepare_rerender(render_context)

        if render_context.render_range is not None and render_context.render_range[0] != plain_spec.get_first_frid(
            render_context.plain_source_tree
        ):
            frid = render_context.render_range[0]

            render_context.starting_frid = frid

            previous_frid = plain_spec.get_previous_frid(render_context.plain_source_tree, frid)

            console.debug(f"Reverting code to version implemented for {previous_frid}.")

            git_utils.revert_to_commit_with_frid(render_context.build_folder, previous_frid)
            # conformance tests are still not fully implemented
            if render_context.render_conformance_tests:
                git_utils.revert_to_commit_with_frid(
                    render_context.conformance_tests.get_module_conformance_tests_folder(render_context.module_name),
                    previous_frid,
                )

        else:
            module_hashes = render_context.plain_module.get_hashes()
            initial_files = {
                render_context.plain_module.module_metadata_path(for_git_repo=True): json.dumps(module_hashes)
            }

            if render_context.required_modules:
                previous_module = render_context.required_modules[-1]
                console.debug(f"Cloning git repo from module {previous_module.module_name}.")

                file_utils.delete_folder(render_context.build_folder)
                git_utils.clone_repo(
                    previous_module.module_build_folder,
                    render_context.build_folder,
                    render_context.module_name,
                    render_context.run_state.render_id,
                    initial_files,
                )
            else:
                console.debug("Initializing git repositories for the render folders.")

                git_utils.init_git_repo(
                    render_context.build_folder,
                    render_context.module_name,
                    render_context.run_state.render_id,
                    initial_files,
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
                    initial_files,
                )

        return self.SUCCESSFUL_OUTCOME, None

    def _prepare_rerender(self, render_context: RenderContext):
        frid = render_context.render_range[0]

        if "." in frid:
            render_context.dispatch_error(
                f"--rerender only supports top-level integer FRIDs (e.g. `1`, `2`). "
                f"Nested FRID `{frid}` is not supported."
            )
            return "error", None

        if not git_utils.has_commit_for_frid(render_context.build_folder, frid, render_context.module_name):
            render_context.dispatch_error(
                f"Cannot re-render functionality {frid} because it has not been fully rendered yet. "
                f"Please render all functionalities first by running: "
                f"codeplain {render_context.module_name}.plain"
            )
            return "error", None

        render_context.starting_frid = frid

        module_metadata = render_context.plain_module.load_module_metadata()
        if not module_metadata or "functionalities" not in module_metadata:
            render_context.dispatch_error(
                "module_metadata.json is missing or incomplete. "
                "Please re-render the module from the beginning."
            )
            return "error", None

        render_context.old_frid_spec = module_metadata["functionalities"][int(frid) - 1]

        if render_context.render_conformance_tests:
            conformance_tests_json = render_context.conformance_tests.get_conformance_tests_json(
                render_context.module_name
            )
            if frid in conformance_tests_json:
                old_folder = conformance_tests_json[frid]["folder_name"]
                file_utils.delete_folder(old_folder)
                del conformance_tests_json[frid]
                render_context.conformance_tests.dump_conformance_tests_json(
                    render_context.module_name, conformance_tests_json
                )

        return self.SUCCESSFUL_OUTCOME, None
