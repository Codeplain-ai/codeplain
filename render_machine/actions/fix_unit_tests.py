from typing import Any

import diff_utils
import file_utils
import render_machine.render_utils as render_utils
from memory_management import InterventionTarget, bound_diff
from plain2code_console import console
from plain2code_exceptions import InternalClientError
from render_machine.actions.base_action import BaseAction
from render_machine.implementation_code_helpers import ImplementationCodeHelpers
from render_machine.render_context import RenderContext
from render_machine.render_types import PendingIntervention

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

        memory_files_content = render_context.retrieve_memory_for_unittest_failure(previous_unittests_issue)

        response_files = render_context.codeplain_api.fix_unittests_issue(
            render_context.frid_context.frid,
            render_context.plain_source_tree,
            render_context.frid_context.linked_resources,
            existing_files_content,
            memory_files_content,
            render_context.module_name,
            render_context.get_required_modules_functionalities(),
            previous_unittests_issue,
            run_state=render_context.run_state,
        )

        _, changed_files = file_utils.update_build_folder_with_rendered_files(
            render_context.build_folder, existing_files, response_files
        )

        render_context.unit_tests_running_context.changed_files.update(changed_files)
        self._remember_intervention(render_context, response_files, existing_files_content, previous_unittests_issue)

        console.print_files("Files fixed:", render_context.build_folder, response_files, style=console.OUTPUT_STYLE)

        return self.SUCCESSFUL_OUTCOME, None

    @staticmethod
    def _remember_intervention(
        render_context: RenderContext,
        response_files: dict,
        existing_files_content: dict,
        failure_output: str,
    ) -> None:
        """Record what was just changed, to be paired with the next unit-test run's outcome.

        The target is left unclassified: a unit-test fix arrives as one set of files with no
        indication of which are implementation and which are tests, and deciding that from
        file paths would be a guess rather than an observation.
        """
        code_diff_files = diff_utils.get_code_diff(response_files, existing_files_content)
        running_context = render_context.unit_tests_running_context
        running_context.pending_intervention = PendingIntervention(
            attempt_index=running_context.fix_attempts,
            target=InterventionTarget.UNCLASSIFIED.value,
            files_changed=sorted(code_diff_files.keys()),
            lines_changed=sum(len(diff.splitlines()) for diff in code_diff_files.values()),
            diff=bound_diff(code_diff_files),
            failure_output=failure_output,
            failure_output_path=render_context.script_execution_history.latest_unit_test_output_path,
        )
