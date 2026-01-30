from typing import Any

import file_utils
import render_machine.render_utils as render_utils
from plain2code_console import console
from plain2code_exceptions import InternalClientError
from render_machine.actions.base_action import BaseAction
from render_machine.implementation_code_helpers import ImplementationCodeHelpers
from render_machine.render_context import RenderContext

MAX_ISSUE_LENGTH = 10000


class FixUnitTests(BaseAction):
    SUCCESSFUL_OUTCOME = "unit_tests_fix_generated"

    def execute(self, render_context: RenderContext, previous_action_payload: Any | None):
        if not previous_action_payload.get("previous_unittests_issue"):
            raise InternalClientError("Previous action payload does not contain previous unit tests issue.")
        previous_unittests_issue = previous_action_payload["previous_unittests_issue"]

        if previous_unittests_issue and len(previous_unittests_issue) > MAX_ISSUE_LENGTH:
            console.warning(
                f"Unit tests issue text is too long and will be smartly truncated to {MAX_ISSUE_LENGTH} characters."
            )

        existing_files, existing_files_content = ImplementationCodeHelpers.fetch_existing_files(
            render_context.build_folder
        )

        if render_context.verbose:
            render_utils.print_inputs(
                render_context, existing_files_content, "Files sent as input to unit tests fixing:"
            )

        with console.status(
            f"[{console.INFO_STYLE}]Fixing unit tests issue for functional requirement {render_context.frid_context.frid}...\n"
        ):
            response_files = render_context.codeplain_api.fix_unittests_issue(
                render_context.frid_context.frid,
                render_context.plain_source_tree,
                render_context.frid_context.linked_resources,
                existing_files_content,
                render_context.module_name,
                render_context.get_required_modules_functionalities(),
                previous_unittests_issue,
                run_state=render_context.run_state,
            )

        _, changed_files = file_utils.update_build_folder_with_rendered_files(
            render_context.build_folder, existing_files, response_files
        )

        render_context.unit_tests_running_context.changed_files.update(changed_files)

        if render_context.verbose:
            console.print_files("Files fixed:", render_context.build_folder, response_files, style=console.OUTPUT_STYLE)

        return self.SUCCESSFUL_OUTCOME, None
