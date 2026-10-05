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
            raise InternalClientError(
                "Internal client error: Previous action payload does not contain previous unit tests issue."
            )
        previous_unittests_issue = previous_action_payload["previous_unittests_issue"]

        if previous_unittests_issue and len(previous_unittests_issue) > MAX_ISSUE_LENGTH:
            console.debug(
                f"Unit tests issue text is too long and will be smartly truncated to {MAX_ISSUE_LENGTH} characters."
            )

        existing_files, existing_files_content = ImplementationCodeHelpers.fetch_existing_files(
            render_context.build_folder
        )

        render_utils.print_inputs(render_context, existing_files_content, "Files sent as input to unit tests fixing:")

        conformance_tests_fixes = self._get_conformance_tests_fixes(render_context)
        if conformance_tests_fixes:
            console.info(
                f"Unit tests are fixed while preserving {len(conformance_tests_fixes)} implementation code change(s) "
                "made to fix the conformance tests."
            )

        response_files = render_context.codeplain_api.fix_unittests_issue(
            render_context.frid_context.frid,
            render_context.plain_source_tree,
            render_context.frid_context.linked_resources,
            existing_files_content,
            render_context.module_name,
            render_context.get_required_modules_functionalities(),
            previous_unittests_issue,
            run_state=render_context.run_state,
            conformance_tests_fixes=conformance_tests_fixes,
        )

        _, changed_files = file_utils.update_build_folder_with_rendered_files(
            render_context.build_folder, existing_files, response_files
        )

        render_context.unit_tests_running_context.changed_files.update(changed_files)

        console.print_files("Files fixed:", render_context.build_folder, response_files, style=console.OUTPUT_STYLE)

        return self.SUCCESSFUL_OUTCOME, None

    @staticmethod
    def _get_conformance_tests_fixes(render_context: RenderContext) -> list[dict] | None:
        """Implementation code changes the conformance tests fixer made before these unit tests were run.

        Only present when unit tests are processed inside the conformance tests phase - the implementation
        and refactoring unit test passes have no conformance tests running context.
        """
        conformance_tests_running_context = getattr(render_context, "conformance_tests_running_context", None)
        if conformance_tests_running_context is None:
            return None

        implementation_code_fixes = getattr(conformance_tests_running_context, "implementation_code_fixes", None)
        if not implementation_code_fixes:
            return None

        return list(implementation_code_fixes)
