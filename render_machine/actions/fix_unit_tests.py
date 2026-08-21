from typing import Any

import diff_utils
import file_utils
import render_machine.render_utils as render_utils
from memory_management import MemoryManager
from plain2code_console import console
from plain2code_exceptions import InternalClientError
from render_machine.actions.base_action import BaseAction
from render_machine.implementation_code_helpers import ImplementationCodeHelpers
from render_machine.render_context import RenderContext

MAX_ISSUE_LENGTH = 10000


class FixUnitTests(BaseAction):
    SUCCESSFUL_OUTCOME = "unit_tests_fix_generated"

    def __init__(self, inside_conformance_fix_loop: bool = False):
        """``inside_conformance_fix_loop`` marks the instance that runs while :ConformanceTests: are being
        fixed. There, the implementation has just been changed on purpose to make a conformance test pass, so a
        unit test failing as a result is asserting behaviour that the change replaced - and repairing the
        implementation restores the conformance failure. The two loops then trade the same lines back and
        forth, neither able to see the other's history."""
        self.inside_conformance_fix_loop = inside_conformance_fix_loop

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

        _, memory_files_content = MemoryManager.fetch_memory_files(render_context.memory_manager.memory_folder)

        conformance_fix_changes = None
        if self.inside_conformance_fix_loop:
            conformance_fix_changes = render_context.conformance_tests_running_context.code_diff_files

        response_files = render_context.codeplain_api.fix_unittests_issue(
            render_context.frid_context.frid,
            render_context.plain_source_tree,
            render_context.frid_context.linked_resources,
            existing_files_content,
            render_context.module_name,
            render_context.get_required_modules_functionalities(),
            previous_unittests_issue,
            memory_files_content,
            conformance_fix_changes,
            run_state=render_context.run_state,
        )

        _, changed_files = file_utils.update_build_folder_with_rendered_files(
            render_context.build_folder, existing_files, response_files
        )

        render_context.unit_tests_running_context.changed_files.update(changed_files)

        # Held for the next unit test run to record: the failure this round answered, and what it changed.
        render_context.unit_tests_running_context.previous_unittests_issue = previous_unittests_issue
        render_context.unit_tests_running_context.code_diff_files = diff_utils.get_code_diff(
            response_files, existing_files_content
        )

        console.print_files("Files fixed:", render_context.build_folder, response_files, style=console.OUTPUT_STYLE)

        return self.SUCCESSFUL_OUTCOME, None
