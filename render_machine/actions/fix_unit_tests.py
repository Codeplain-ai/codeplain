from typing import Any, Optional

import diff_utils
import failure_signature
import file_utils
import render_machine.render_utils as render_utils
from conformance_test_journal import (
    LOOP_UNIT,
    PHASE_IMPLEMENTATION,
    VERDICT_UNIT_TESTS,
    ConformanceTestJournal,
    build_issue_excerpt,
    compute_spec_hash,
)
from plain2code_console import console
from plain2code_exceptions import InternalClientError
from render_machine.actions.base_action import BaseAction
from render_machine.implementation_code_helpers import ImplementationCodeHelpers
from render_machine.render_context import RenderContext

MAX_ISSUE_LENGTH = 10000


class FixUnitTests(BaseAction):
    SUCCESSFUL_OUTCOME = "unit_tests_fix_generated"

    def __init__(self, phase_context: str = PHASE_IMPLEMENTATION):
        """The same action runs in three places; which one it is decides what context the fixer gets.

        Inside a conformance fix loop a failing unit test is usually fallout from a deliberate change, and the
        fixer is told so. In the implementation and refactoring phases the unit tests are the only acceptance
        signal there is, so nothing about them is downplayed.
        """
        self.phase_context = phase_context

    @staticmethod
    def _load_journal(render_context: RenderContext) -> Optional[ConformanceTestJournal]:
        """The journal of the functionality whose tests are being fixed.

        Keyed on the functionality under test while conformance tests are running, so that unit-test rounds
        land in the same journal the conformance fixer reads. Outside that phase there is no conformance
        context, and the functionality being implemented is the only one in play.
        """
        ctx = render_context.conformance_tests_running_context
        if ctx is not None and ctx.current_testing_frid is not None:
            return ConformanceTestJournal.load(
                render_context.memory_manager.memory_folder,
                ctx.current_testing_module_name,
                ctx.current_testing_frid,
                spec_hash=compute_spec_hash(ctx.current_testing_frid_specifications),
            )

        if render_context.frid_context is None or render_context.frid_context.frid is None:
            return None

        return ConformanceTestJournal.load(
            render_context.memory_manager.memory_folder,
            render_context.module_name,
            render_context.frid_context.frid,
            spec_hash=compute_spec_hash(render_context.frid_context.specifications),
        )

    def _record_round(
        self,
        render_context: RenderContext,
        journal: Optional[ConformanceTestJournal],
        unittests_issue: str,
        code_diff_files_content: dict,
    ) -> None:
        """Journal this unit-test round so the conformance fixer can see it.

        Recorded mechanically: there is no reviewer on the unit-test path to describe the failure or the
        change, so the note carries keys and a diff but no tags. That is enough for the links that matter -
        a change that puts back what a conformance fix took out is recognised from the diffs alone.
        """
        if journal is None:
            return

        failure_note_id = journal.record_failure(
            loop=LOOP_UNIT,
            exit_code=1,
            exact_signature=failure_signature.compute_exact_signature(unittests_issue, 1),
            skeleton_signature=failure_signature.compute_skeleton_signature(unittests_issue, 1),
            sketch=failure_signature.compute_sketch(unittests_issue),
            evidence=build_issue_excerpt(unittests_issue),
        )
        journal.record_attempt(
            loop=LOOP_UNIT,
            verdict=VERDICT_UNIT_TESTS,
            code_diff_files_content=code_diff_files_content,
            prompted_by=failure_note_id,
            phase_context=self.phase_context,
        )
        journal.save(render_context.memory_manager.memory_folder)

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

        journal = self._load_journal(render_context)
        recent_implementation_changes = journal.render_recent_implementation_changes() if journal else None

        render_utils.print_inputs(render_context, existing_files_content, "Files sent as input to unit tests fixing:")

        response_files = render_context.codeplain_api.fix_unittests_issue(
            render_context.frid_context.frid,
            render_context.plain_source_tree,
            render_context.frid_context.linked_resources,
            existing_files_content,
            render_context.module_name,
            render_context.get_required_modules_functionalities(),
            previous_unittests_issue,
            phase_context=self.phase_context,
            recent_implementation_changes=recent_implementation_changes,
            run_state=render_context.run_state,
        )

        code_diff_files_content = diff_utils.get_code_diff(response_files, existing_files_content)

        _, changed_files = file_utils.update_build_folder_with_rendered_files(
            render_context.build_folder, existing_files, response_files
        )

        render_context.unit_tests_running_context.changed_files.update(changed_files)

        self._record_round(render_context, journal, previous_unittests_issue, code_diff_files_content)

        console.print_files("Files fixed:", render_context.build_folder, response_files, style=console.OUTPUT_STYLE)

        return self.SUCCESSFUL_OUTCOME, None
