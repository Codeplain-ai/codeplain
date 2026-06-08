import os
from typing import Any

import diff_utils
import file_utils
from plain2code_console import console
from render_machine.actions.base_action import BaseAction
from render_machine.agent import agent_runner
from render_machine.agent.tool_executor import ToolExecutor
from render_machine.agent.tools import grep, list_files, ls_files, read_file, think
from render_machine.render_context import RenderContext


class ReviewConformanceFixAction(BaseAction):
    APPROVED = "fix_approved"
    REJECTED = "fix_rejected"

    def execute(self, render_context: RenderContext, previous_action_payload: Any | None):
        if not previous_action_payload:
            console.error("ReviewConformanceFixAction called without payload")
            return self.REJECTED, {"rejection_feedback": "No fix provided"}

        # Extract data from agent's payload
        file_snapshot = previous_action_payload.get("file_snapshot", {})
        specifications = previous_action_payload.get("specifications", "")
        acceptance_tests = previous_action_payload.get("acceptance_tests", "")
        conformance_test_folder = previous_action_payload.get("conformance_test_folder", "")
        agent_summary = previous_action_payload.get("agent_summary", "")

        console.info("Reviewing conformance test fix...")

        # Compute diff between snapshot and current state
        current_files = {}
        all_impl_files = file_utils.list_all_text_files(render_context.build_folder)
        for file_path in all_impl_files:
            full_path = os.path.join(render_context.build_folder, file_path)
            with open(full_path, "r", encoding="utf-8") as f:
                current_files[file_path] = f.read()

        if conformance_test_folder and os.path.exists(conformance_test_folder):
            ct_files = file_utils.list_all_text_files(conformance_test_folder)
            for file_path in ct_files:
                full_path = os.path.join(conformance_test_folder, file_path)
                with open(full_path, "r", encoding="utf-8") as f:
                    current_files[f"conformance_tests/{file_path}"] = f.read()

        diff_text = diff_utils.get_code_diff(current_files, file_snapshot)
        if not diff_text:
            console.warning("No changes detected in fix. Approving by default.")
            return self.APPROVED, {"rejection_feedback": ""}

        diff_str = ""
        for file_path, file_diff in diff_text.items():
            diff_str += f"--- {file_path}\n{file_diff}\n\n"

        # Get test output file path from context (set by RunConformanceTests)
        test_output_file = render_context.conformance_tests_running_context.test_output_file_path or ""

        # Build task params for the reviewer agent
        review_task_params = {
            "specifications": specifications,
            "acceptance_tests": acceptance_tests,
            "test_output_file": test_output_file,
            "diff": diff_str,
            "explanation": agent_summary,
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
            return self.APPROVED, {"rejection_feedback": ""}
        else:
            console.warning(f"[yellow]Review REJECTED[/yellow]: {result_text}")
            return self.REJECTED, {
                "rejection_feedback": result_text,
                "previous_agent_summary": agent_summary,
            }
