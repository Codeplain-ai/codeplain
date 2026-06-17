from typing import Any

from memory_management import MemoryManager
from plain2code_console import console
from render_machine.actions.base_action import BaseAction
from render_machine.implementation_code_helpers import ImplementationCodeHelpers
from render_machine.render_context import MAX_CONFORMANCE_EVALUATION_ATTEMPTS, RenderContext


class EvaluateImplementation(BaseAction):
    """Step back after repeated failed conformance-test fix attempts and evaluate whether the initial
    implementation code and/or the rendered conformance tests fail to conform to the specifications.

    The evaluation is deliberately NOT given the test failures - its job is to find a lacking initial
    implementation, not to fix the symptom. A fixed policy routes the verdict:
      - IMPLEMENTATION_INCORRECT / BOTH_INCORRECT -> re-implement the functionality from scratch (which also
        re-renders the conformance tests), feeding the feedback in as guidance.
      - CONFORMANCE_TESTS_INCORRECT -> regenerate the conformance tests, keeping the implementation code.
      - NEITHER_INCORRECT (or inconclusive) -> fall back to the normal conformance-test fix loop.
    """

    REIMPLEMENT_IMPLEMENTATION_CODE = "reimplement_implementation_code"
    REGENERATE_CONFORMANCE_TESTS = "regenerate_conformance_tests"
    CONTINUE_FIXING = "continue_fixing"

    VERDICT_IMPLEMENTATION_INCORRECT = "IMPLEMENTATION_INCORRECT"
    VERDICT_CONFORMANCE_TESTS_INCORRECT = "CONFORMANCE_TESTS_INCORRECT"
    VERDICT_BOTH_INCORRECT = "BOTH_INCORRECT"
    VERDICT_NEITHER_INCORRECT = "NEITHER_INCORRECT"

    def execute(self, render_context: RenderContext, previous_action_payload: Any | None):
        ctx = render_context.conformance_tests_running_context
        render_context.conformance_evaluation_attempts += 1

        console.info(
            f"Evaluating the implementation and conformance tests for functionality "
            f"{render_context.frid_context.frid} after {ctx.fix_attempts} failed conformance test fix attempts "
            f"(evaluation {render_context.conformance_evaluation_attempts}/{MAX_CONFORMANCE_EVALUATION_ATTEMPTS})."
        )

        _, existing_files_content = ImplementationCodeHelpers.fetch_existing_files(render_context.build_folder)
        _, memory_files_content = MemoryManager.fetch_memory_files(render_context.memory_manager.memory_folder)
        (
            _,
            existing_conformance_test_files_content,
        ) = render_context.conformance_tests.fetch_existing_conformance_test_files(
            render_context.module_name,
            render_context.required_modules,
            ctx.current_testing_module_name,
            ctx.get_current_conformance_test_folder_name(),
        )
        code_diff = ImplementationCodeHelpers.get_code_diff(
            render_context.build_folder, render_context.plain_source_tree, render_context.frid_context.frid
        )

        evaluation = render_context.codeplain_api.evaluate_conformance_implementation(
            render_context.frid_context.frid,
            render_context.plain_source_tree,
            render_context.frid_context.linked_resources,
            existing_files_content,
            memory_files_content,
            render_context.module_name,
            render_context.get_required_modules_functionalities(),
            code_diff,
            existing_conformance_test_files_content,
            ctx.get_current_acceptance_tests(),
            run_state=render_context.run_state,
        )

        if not evaluation:
            console.info("Implementation evaluation was inconclusive. Continuing to fix the conformance tests.")
            return self.CONTINUE_FIXING, previous_action_payload

        verdict = evaluation["verdict"]
        feedback = evaluation.get("feedback")
        render_context.evaluation_feedback = feedback

        console.info(f"Implementation evaluation verdict: {verdict}.")
        if feedback:
            console.info(f"Evaluation feedback:\n{feedback}")

        if verdict in (self.VERDICT_IMPLEMENTATION_INCORRECT, self.VERDICT_BOTH_INCORRECT):
            console.info(
                f"Re-implementing functionality {render_context.frid_context.frid} from scratch based on the "
                f"evaluation feedback."
            )
            # The re-implementation is a fresh attempt informed by the evaluation feedback, so give it a clean
            # code-generation retry budget. Otherwise the restart is counted against MAX_CODE_GENERATION_RETRIES
            # (which bounds unit-test-fix restarts) and the functionality fails prematurely. Eval-driven restarts
            # are independently bounded by MAX_CONFORMANCE_EVALUATION_ATTEMPTS.
            render_context.frid_context.functional_requirement_render_attempts = 0
            return self.REIMPLEMENT_IMPLEMENTATION_CODE, None

        if verdict == self.VERDICT_CONFORMANCE_TESTS_INCORRECT:
            console.info(
                f"Regenerating the conformance tests for functionality {render_context.frid_context.frid} based on "
                f"the evaluation feedback."
            )
            ctx.regenerating_conformance_tests = True
            return self.REGENERATE_CONFORMANCE_TESTS, None

        console.info(
            "Implementation and conformance tests appear to conform to the specifications. Continuing to fix the "
            "conformance tests."
        )
        return self.CONTINUE_FIXING, previous_action_payload
