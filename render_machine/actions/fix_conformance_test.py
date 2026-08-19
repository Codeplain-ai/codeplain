from typing import Any, Optional

import diff_utils
import file_utils
import plain_spec
from conformance_test_journal import (
    LOOP_CONFORMANCE,
    PHASE_INSIDE_CONFORMANCE_FIX,
    PROMPT_FILE_NAME,
    ROLE_IMPLEMENTATION,
    ROLE_TEST,
    VERDICT_CONFLICTING_ACCEPTANCE_TESTS,
    VERDICT_CONFLICTING_REQUIREMENTS,
    VERDICT_CONFORMANCE_TESTS,
    VERDICT_IMPLEMENTATION_CODE,
    ConformanceTestJournal,
    compute_spec_hash,
)
from memory_management import MemoryManager
from plain2code_console import RETRY_COLOR, console
from plain2code_exceptions import InternalClientError
from render_machine.actions.base_action import BaseAction
from render_machine.implementation_code_helpers import ImplementationCodeHelpers
from render_machine.render_context import RenderContext
from render_machine.render_types import RenderError, TestExecutionPhase

MAX_CONFORMANCE_TEST_FIX_ATTEMPTS = 20
MAX_CONFORMANCE_TEST_RERENDER_ATTEMPTS = 1


class FixConformanceTest(BaseAction):
    IMPLEMENTATION_CODE_NOT_UPDATED = "implementation_code_not_updated"
    IMPLEMENTATION_CODE_UPDATED = "implementation_code_updated"
    LIMIT_EXCEEDED_OUTCOME = "conformance_test_fix_limit_exceeded"
    REGENERATE_CONFORMANCE_TESTS_OUTCOME = "regenerate_conformance_tests"

    ISSUE_REASON_CODE_CONFORMANCE_TESTS = 0
    ISSUE_REASON_CODE_IMPLEMENTATION_CODE = 1
    ISSUE_REASON_CODE_CONFLICTING_REQUIREMENTS = 2
    ISSUE_REASON_CODE_CONFLICTING_ACCEPTANCE_TESTS = 3

    @staticmethod
    def _load_journal(render_context: RenderContext) -> Optional[ConformanceTestJournal]:
        """The journal for the functionality under test, or None when no functionality is under test."""
        ctx = render_context.conformance_tests_running_context
        if ctx.current_testing_frid is None:
            return None

        return ConformanceTestJournal.load(
            render_context.memory_manager.memory_folder,
            ctx.current_testing_module_name,
            ctx.current_testing_frid,
            spec_hash=compute_spec_hash(ctx.current_testing_frid_specifications),
        )

    VERDICT_BY_ISSUE_REASON_CODE = {
        ISSUE_REASON_CODE_CONFORMANCE_TESTS: VERDICT_CONFORMANCE_TESTS,
        ISSUE_REASON_CODE_IMPLEMENTATION_CODE: VERDICT_IMPLEMENTATION_CODE,
        ISSUE_REASON_CODE_CONFLICTING_REQUIREMENTS: VERDICT_CONFLICTING_REQUIREMENTS,
        ISSUE_REASON_CODE_CONFLICTING_ACCEPTANCE_TESTS: VERDICT_CONFLICTING_ACCEPTANCE_TESTS,
    }

    @classmethod
    def _record_round(
        cls,
        render_context: RenderContext,
        issue_reason_code: int,
        code_diff_files_content: dict,
        failure_note: Optional[dict],
    ) -> None:
        """Journal this round: the failure that prompted it, and the change made in response.

        Recorded after the fix rather than before it, because only then is it known what the fix touched. The
        keys identifying the failure are computed locally when the tests are run; the description of it comes
        from the model that reviewed the fix, which read the same output.
        """
        ctx = render_context.conformance_tests_running_context
        journal = cls._load_journal(render_context)
        if journal is None:
            return

        tags = failure_note or {}
        failure_note_id = journal.record_failure(
            loop=LOOP_CONFORMANCE,
            exit_code=ctx.last_failure_exit_code if ctx.last_failure_exit_code is not None else 1,
            exact_signature=ctx.last_failure_signature,
            skeleton_signature=ctx.last_failure_skeleton_signature,
            sketch=ctx.last_failure_sketch,
            distinctive_signature=ctx.last_failure_distinctive_signature,
            tags=tags.get("failure_tags"),
            statement=tags.get("failure_statement"),
            evidence=tags.get("failure_evidence") or ctx.last_failure_excerpt,
            canonical_fingerprint=tags.get("canonical_fingerprint"),
        )
        ctx.last_failure_note_id = failure_note_id

        verdict = cls.VERDICT_BY_ISSUE_REASON_CODE.get(issue_reason_code, VERDICT_IMPLEMENTATION_CODE)
        journal.record_attempt(
            loop=LOOP_CONFORMANCE,
            verdict=verdict,
            code_diff_files_content=code_diff_files_content,
            prompted_by=failure_note_id,
            phase_context=PHASE_INSIDE_CONFORMANCE_FIX,
            default_role=ROLE_TEST if verdict == VERDICT_CONFORMANCE_TESTS else ROLE_IMPLEMENTATION,
            tags=tags.get("fix_tags"),
            rationale=tags.get("fix_rationale"),
        )
        journal.save(render_context.memory_manager.memory_folder)

    def execute(self, render_context: RenderContext, previous_action_payload: Any | None):
        ctx = render_context.conformance_tests_running_context
        ctx.fix_attempts += 1

        if ctx.fix_attempts >= MAX_CONFORMANCE_TEST_FIX_ATTEMPTS:
            if ctx.conformance_tests_render_attempts >= MAX_CONFORMANCE_TEST_RERENDER_ATTEMPTS:
                error_msg = f"The renderer was unable to produce an implementation that passes conformance tests for functionality '{render_context.frid_context.frid}' after many attempts. Please review and rewrite the specification. (Render ID: {render_context.run_state.render_id})"
                render_context.last_error_message = error_msg
                return (
                    self.LIMIT_EXCEEDED_OUTCOME,
                    RenderError.encode(message=error_msg).to_payload(),
                )
            else:
                ctx.regenerating_conformance_tests = True
                return self.REGENERATE_CONFORMANCE_TESTS_OUTCOME, None

        console.info(f"Running conformance tests attempt {ctx.fix_attempts + 1}.")

        console.info(
            f"Fixing conformance test for functionality {render_context.conformance_tests_running_context.current_testing_frid} in module {render_context.conformance_tests_running_context.current_testing_module_name}."
        )

        if not previous_action_payload.get("previous_conformance_tests_issue"):
            raise InternalClientError(
                "Internal client error: Previous action payload does not contain previous conformance tests issue."
            )
        previous_conformance_tests_issue = previous_action_payload["previous_conformance_tests_issue"]

        existing_files, existing_files_content = ImplementationCodeHelpers.fetch_existing_files(
            render_context.build_folder
        )
        _, memory_files_content = MemoryManager.fetch_memory_files(render_context.memory_manager.memory_folder)

        # What has already been tried for this functionality, so that exhausted approaches are not repeated.
        journal = self._load_journal(render_context)
        previous_attempts = journal.render_for_prompt() if journal else None
        if previous_attempts:
            memory_files_content[PROMPT_FILE_NAME] = previous_attempts

        (
            existing_conformance_test_files,
            existing_conformance_test_files_content,
        ) = render_context.conformance_tests.fetch_existing_conformance_test_files(
            render_context.module_name,
            render_context.required_modules,
            render_context.conformance_tests_running_context.current_testing_module_name,
            render_context.conformance_tests_running_context.get_current_conformance_test_folder_name(),
        )
        previous_frid_code_diff = ImplementationCodeHelpers.get_code_diff(
            render_context.build_folder, render_context.plain_source_tree, render_context.frid_context.frid
        )

        conflicting_module_name = render_context.conformance_tests_running_context.conflicting_module_name
        conflicting_frid = render_context.conformance_tests_running_context.conflicting_frid
        current_testing_module_name = render_context.conformance_tests_running_context.current_testing_module_name
        current_testing_frid = render_context.conformance_tests_running_context.current_testing_frid

        # Reset the conflicting requirement count if the current testing functionality is not the same as the previously conflicting functionality
        if conflicting_module_name != current_testing_module_name or conflicting_frid != current_testing_frid:
            render_context.conformance_tests_running_context.conflicting_requirement_count = 0

        tmp_resources_list = []
        plain_spec.collect_linked_resources(
            render_context.plain_source_tree,
            tmp_resources_list,
            None,
            False,
            render_context.frid_context.frid,
        )
        console.print_resources(tmp_resources_list, render_context.frid_context.linked_resources)

        console.print_files(
            "Implementation files sent as input for fixing conformance tests issues:",
            render_context.build_folder,
            existing_files_content,
            style=console.INPUT_STYLE,
        )

        console.print_files(
            "Conformance tests files sent as input for fixing conformance tests issues:",
            render_context.conformance_tests_running_context.get_current_conformance_test_folder_name(),
            existing_conformance_test_files_content,
            style=console.INPUT_STYLE,
        )

        fix_result = render_context.codeplain_api.fix_conformance_tests_issue(
            render_context.frid_context.frid,
            render_context.conformance_tests_running_context.current_testing_frid,
            render_context.plain_source_tree,
            render_context.frid_context.linked_resources,
            existing_files_content,
            memory_files_content,
            render_context.module_name,
            render_context.conformance_tests_running_context.current_testing_module_name,
            render_context.get_required_modules_functionalities(),
            previous_frid_code_diff,
            existing_conformance_test_files_content,
            render_context.conformance_tests_running_context.get_current_acceptance_tests(),
            previous_conformance_tests_issue,
            render_context.conformance_tests_running_context.fix_attempts,
            render_context.conformance_tests_running_context.get_current_conformance_test_folder_name(),
            render_context.conformance_tests_running_context.current_testing_frid_high_level_implementation_plan,
            render_context.conformance_tests_running_context.conflicting_requirement_count,
            run_state=render_context.run_state,
        )
        # A third element carries the round's failure note. Unpacked tolerantly so that a server which does
        # not yet send one degrades to a journal without descriptions rather than to a crash.
        issue_reason_code, response_files = fix_result[0], fix_result[1]
        failure_note = fix_result[2] if len(fix_result) > 2 else None

        code_diff_files_content = {}

        if (
            issue_reason_code == self.ISSUE_REASON_CODE_CONFLICTING_REQUIREMENTS
            or issue_reason_code == self.ISSUE_REASON_CODE_CONFLICTING_ACCEPTANCE_TESTS
        ):
            render_context.conformance_tests_running_context.conflicting_requirement_count += 1
            render_context.conformance_tests_running_context.conflicting_module_name = current_testing_module_name
            render_context.conformance_tests_running_context.conflicting_frid = current_testing_frid
            console.info(
                f"↻ Potential conflicting functionalities detected while fixing conformance tests "
                f"for functionality {current_testing_frid} in module {current_testing_module_name}.",
                color=RETRY_COLOR,
            )

        if issue_reason_code == self.ISSUE_REASON_CODE_CONFORMANCE_TESTS:
            render_context.conformance_tests.store_conformance_tests_files(
                render_context.module_name,
                render_context.required_modules,
                render_context.conformance_tests_running_context.current_testing_module_name,
                render_context.conformance_tests_running_context.get_current_conformance_test_folder_name(),
                response_files,
                existing_conformance_test_files,
            )
            code_diff_files_content = diff_utils.get_code_diff(response_files, existing_conformance_test_files_content)
            render_context.conformance_tests_running_context.code_diff_files = code_diff_files_content

            self._record_round(render_context, issue_reason_code, code_diff_files_content, failure_note)

            return self.IMPLEMENTATION_CODE_NOT_UPDATED, None
        else:
            if len(response_files) > 0:
                file_utils.store_response_files(render_context.build_folder, response_files, existing_files)
                code_diff_files_content = diff_utils.get_code_diff(response_files, existing_files_content)
                render_context.conformance_tests_running_context.code_diff_files = code_diff_files_content
                console.print_files(
                    "Files fixed:",
                    render_context.build_folder,
                    response_files,
                    style=console.OUTPUT_STYLE,
                )
                render_context.conformance_tests_running_context.should_prepare_testing_environment = True

                # Record which test triggered the change and transition to retry phase
                ctx = render_context.conformance_tests_running_context
                ctx.test_that_triggered_code_change = (ctx.current_testing_module_name, ctx.current_testing_frid)
                ctx.execution_phase = TestExecutionPhase.RETRYING_AFTER_CODE_CHANGE

                self._record_round(render_context, issue_reason_code, code_diff_files_content, failure_note)

                return self.IMPLEMENTATION_CODE_UPDATED, None
            else:
                # A round that changed nothing is still a round, and knowing it happened is what stops the
                # same barren approach being taken again.
                self._record_round(render_context, issue_reason_code, {}, failure_note)

                return self.IMPLEMENTATION_CODE_NOT_UPDATED, None
