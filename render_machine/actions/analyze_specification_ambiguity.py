from typing import Any

import file_utils
import git_utils
import plain_spec
from plain2code_console import console
from plain2code_exceptions import InternalClientError
from plain2code_utils import AMBIGUITY_CAUSES
from render_machine.actions.base_action import BaseAction
from render_machine.render_context import RenderContext


class AnalyzeSpecificationAmbiguity(BaseAction):
    SUCCESSFUL_OUTCOME = "conformance_tests_postanalyzed"

    def execute(self, render_context: RenderContext, _previous_action_payload: Any | None):
        fixed_implementation_code_diff = git_utils.get_fixed_implementation_code_diff(
            render_context.build_folder, render_context.frid_context.frid
        )
        if fixed_implementation_code_diff is None:
            raise InternalClientError(
                "Fixes to the implementation code found during conformance testing are not committed to git."
            )
        previous_frid = plain_spec.get_previous_frid(render_context.plain_source_tree, render_context.frid_context.frid)
        git_utils.checkout_commit_with_frid(render_context.build_folder, previous_frid)
        existing_files = file_utils.list_all_text_files(render_context.build_folder)
        existing_files_content = file_utils.get_existing_files_content(render_context.build_folder, existing_files)
        git_utils.checkout_previous_branch(render_context.build_folder)
        implementation_code_diff = git_utils.get_implementation_code_diff(
            render_context.build_folder, render_context.frid_context.frid, previous_frid
        )
        rendering_analysis = render_context.codeplain_api.analyze_rendering(
            render_context.frid_context.frid,
            render_context.plain_source_tree,
            render_context.frid_context.linked_resources,
            existing_files_content,
            render_context.module_name,
            render_context.get_required_modules_functionalities(),
            implementation_code_diff,
            fixed_implementation_code_diff,
            run_state=render_context.run_state,
        )
        if rendering_analysis:
            # TODO: Before this output is exposed to the user, we should check the 'guidance' field using LLM in the same way as we do conflicting requirements.
            console.info(
                f"Specification ambiguity detected! {AMBIGUITY_CAUSES[rendering_analysis['cause']]} of the functional requirement {render_context.frid_context.frid}."
            )
            console.info(rendering_analysis["guidance"])
        else:
            console.warning(
                f"No specification ambiguity detected for functional requirement {render_context.frid_context.frid}."
            )
        return self.SUCCESSFUL_OUTCOME, None
