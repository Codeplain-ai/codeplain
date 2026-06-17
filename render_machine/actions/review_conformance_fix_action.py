import os
from typing import Any

import diff_utils
from plain2code_console import console
from render_machine.actions.base_action import BaseAction
from render_machine.agent import agent_runner
from render_machine.agent.tool_executor import ToolExecutor
from render_machine.agent.tools import grep, list_files, ls_files, read_file, think
from render_machine.render_context import RenderContext


class ReviewConformanceFixAction(BaseAction):
    APPROVED = "fix_approved"
    REJECTED = "fix_rejected"

    def execute(self, render_context: RenderContext, _previous_action_payload: Any | None):
        ctx = render_context.conformance_tests_running_context

        # A fix's integrity is reviewed only after it has already made the conformance
        # tests pass, so this action is reached via the test-run transition rather than
        # directly from the fix agent. The reviewer inputs are therefore read from the
        # context (stashed by the fix agent), not from the previous action's payload.
        review_context = (ctx.fix_review_context if ctx else None) or {}
        specifications = review_context.get("specifications", "")
        acceptance_tests = review_context.get("acceptance_tests", "")
        conformance_test_folder = review_context.get("conformance_test_folder", "")
        fix_summary = ctx.last_fix_summary if ctx else {}

        console.info("Reviewing conformance test fix...")

        # We are now consuming the pending review; clear it so subsequent passing
        # test runs (e.g. during regression) are not re-routed back into review.
        if ctx:
            ctx.pending_fix_review = False

        # Compute diff from the file change tracker (tracks originals before modification)
        if not ctx or not ctx.file_change_tracker:
            console.warning("No changes detected in fix. Approving by default.")
            if ctx:
                render_context.finalize_accepted_conformance_fix()
            return self.APPROVED, {"review_rejection_feedback": ""}

        current_files = {}
        original_files = {}
        for absolute_path, original_content in ctx.file_change_tracker.items():
            relative_path = os.path.relpath(absolute_path)
            if original_content is not None:
                original_files[relative_path] = original_content
            if os.path.exists(absolute_path):
                with open(absolute_path, "r", encoding="utf-8") as f:
                    current_files[relative_path] = f.read()
            else:
                current_files[relative_path] = ""

        diff_text = diff_utils.get_code_diff(current_files, original_files)
        if not diff_text:
            console.warning("No changes detected in fix. Approving by default.")
            ctx.reset_file_change_tracker()
            render_context.finalize_accepted_conformance_fix()
            return self.APPROVED, {"review_rejection_feedback": ""}

        diff_str = ""
        for file_path, file_diff in diff_text.items():
            diff_str += f"--- {file_path}\n{file_diff}\n\n"

        # Get test output file path from context (set by RunConformanceTests)
        test_output_file = render_context.script_execution_history.latest_conformance_test_output_path or ""
        prepare_environment_output_file = (
            render_context.script_execution_history.latest_testing_environment_output_path or ""
        )

        # Build explanation from structured fix summary
        explanation = ""
        if fix_summary:
            explanation = (
                f"Root cause: {fix_summary.get('root_cause', 'N/A')}\nChanges: {fix_summary.get('changes_made', 'N/A')}"
            )

        # Build task params for the reviewer agent
        review_task_params = {
            "specifications": specifications,
            "acceptance_tests": acceptance_tests,
            "test_output_file": test_output_file,
            "prepare_environment_output_file": prepare_environment_output_file,
            "diff": diff_str,
            "explanation": explanation,
            "conformance_tests_script_path": render_context.conformance_tests_script or "",
            "prepare_environment_script_path": render_context.prepare_environment_script or "",
            "build_folder": render_context.build_folder,
            "conformance_tests_folder": conformance_test_folder,
            "module_name": render_context.module_name,
        }

        # Reviewer gets read-only tools
        reviewer_tools = {
            "think": think,
            "read_file": read_file,
            "list_files": list_files,
            "ls_files": ls_files,
            "grep": grep,
        }
        reviewer_executor = ToolExecutor(available_tools=reviewer_tools)

        # Run the reviewer agent to completion (with lower turn limit for reviewers)
        response = agent_runner.run(
            "review_conformance_fix",
            review_task_params,
            render_context,
            reviewer_executor,
            max_turns=agent_runner.MAX_REVIEWER_TURNS,
        )

        # Parse the reviewer's final response for VERDICT
        result_text = response.get("result", "")
        if "VERDICT: APPROVED" in result_text.upper():
            console.info("[green]Review APPROVED[/green]")
            ctx.reset_file_change_tracker()
            # The fix is accepted and the tests already pass: release the fix agent
            # session and clear unresolved memory, the cleanup previously done by
            # RunConformanceTests on a passing run (now deferred until acceptance).
            render_context.finalize_accepted_conformance_fix()
            return self.APPROVED, {"review_rejection_feedback": ""}

        console.warning(f"[yellow]Review REJECTED[/yellow]: {result_text}")
        console.info("Reverting rejected changes...")
        ctx.revert_tracked_changes()
        result_text += "\nThe rejected changes have been reverted. Propose a new fix."
        return self.REJECTED, {
            "review_rejection_feedback": result_text,
        }
