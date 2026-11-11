from typing import Any

import file_utils
import git_utils
import plain_spec
from memory_management import MemoryManager
from plain2code_console import console
from render_machine.actions.base_action import BaseAction
from render_machine.render_context import RenderContext
from render_machine.render_types import RenderError


class PrepareRepositories(BaseAction):
    SUCCESSFUL_OUTCOME = "repositories_prepared"
    REGENERATE_NOT_POSSIBLE_OUTCOME = "regenerate_not_possible"

    def execute(self, render_context: RenderContext, _previous_action_payload: Any | None):
        if render_context.is_regenerate:
            return self._prepare_regenerate(render_context)

        if render_context.render_range is not None and render_context.render_range[0] != plain_spec.get_first_frid(
            render_context.plain_source_tree
        ):
            frid = render_context.render_range[0]

            render_context.starting_frid = frid

            previous_frid = plain_spec.get_previous_frid(render_context.plain_source_tree, frid)

            console.debug(
                f"Reverting code to version implemented for {render_context.functionality_label(previous_frid)}."
            )

            render_context.plain_module.revert_code_to_frid(previous_frid)
            # conformance tests are still not fully implemented
            if render_context.render_conformance_tests:
                git_utils.revert_to_commit_with_frid(
                    render_context.conformance_tests.get_module_conformance_tests_folder(render_context.module_name),
                    previous_frid,
                )

        else:
            if render_context.required_modules:
                # If the required module exists only as a "<module>.module" archive, extract it to scratch first
                # (no-op if it is already an unpacked folder or already materialized).
                render_context.required_modules[-1].materialize()

            file_utils.delete_folder(render_context.plain_module.module_folder)
            render_context.plain_module.seed_module_metadata()

            if render_context.required_modules:
                previous_module = render_context.required_modules[-1]
                console.debug(f"Cloning git repo from module {previous_module.module_name}.")

                git_utils.clone_repo(
                    previous_module.module_build_folder,
                    render_context.build_folder,
                    render_context.module_name,
                    render_context.run_state.render_id,
                )

                # Like the code repo, the distilled conformance test memories carry over from the
                # previous module so its learnings are available while rendering this one.
                MemoryManager.inherit_memories(
                    previous_module.module_memory_folder,
                    render_context.plain_module.module_memory_folder,
                )
            else:
                console.debug("Initializing git repositories for the render folders.")

                git_utils.init_git_repo(
                    render_context.build_folder,
                    render_context.module_name,
                    render_context.run_state.render_id,
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

    def _regenerate_not_possible(self, render_context: RenderContext, error_message: str):
        # The error goes back as the outcome, so the state machine runs ExitWithError, which logs it.
        # Dispatching the error from inside this action would end the render before that step runs,
        # and the user would see no reason.
        render_context.last_error_message = error_message
        return self.REGENERATE_NOT_POSSIBLE_OUTCOME, RenderError.encode(message=error_message).to_payload()

    def _prepare_regenerate(self, render_context: RenderContext):
        frid = render_context.render_range[0]

        if not git_utils.has_commit_for_frid(render_context.build_folder, frid, render_context.module_name):
            return self._regenerate_not_possible(
                render_context,
                f"Cannot regenerate functionality {frid} because it has not been fully rendered yet. "
                f"Please render all functionalities first by running: "
                f"codeplain {render_context.module_name}.plain",
            )

        render_context.starting_frid = frid

        module_metadata = render_context.plain_module.load_module_metadata()
        if not module_metadata or "functionalities" not in module_metadata:
            return self._regenerate_not_possible(
                render_context,
                "module_metadata.json is missing or incomplete. Please re-render the module from the beginning.",
            )

        stored_functionalities = module_metadata["functionalities"]
        old_functionality = next((stored for stored in stored_functionalities if stored["id"] == frid), None)
        if old_functionality is None:
            return self._regenerate_not_possible(
                render_context,
                f"module_metadata.json holds no functionality {frid}. "
                "Please re-render the module from the beginning.",
            )

        render_context.old_frid_spec = old_functionality["markdown"]
        # The specs the code on disk currently implements (code-truth). Sent to the API so the
        # regeneration prompt shows the old spec for any changed-but-not-yet-regenerated FR,
        # keeping specs and code in sync.
        render_context.code_functional_requirements = [stored["markdown"] for stored in stored_functionalities]

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
