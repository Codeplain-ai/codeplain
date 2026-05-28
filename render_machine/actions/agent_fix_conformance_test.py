import os
import tempfile
from typing import Any

import file_utils
import plain_spec
from plain2code_console import console
from render_machine.actions.base_action import BaseAction
from render_machine.agent import agent_runner
from render_machine.agent.tool_executor import ToolExecutor
from render_machine.agent.tools import (
    create_submit_fix_for_review,
    grep,
    list_files,
    ls_files,
    prepare_environment,
    read_file,
    run_conformance_tests,
    write_file,
)
from render_machine.implementation_code_helpers import ImplementationCodeHelpers
from render_machine.render_context import RenderContext


class AgentFixConformanceTest(BaseAction):
    FIX_READY_FOR_REVIEW = "fix_ready_for_review"

    def execute(self, render_context: RenderContext, previous_action_payload: Any | None):
        # Gather all feedback from previous iterations
        previous_conformance_tests_issue = (
            previous_action_payload.get("previous_conformance_tests_issue", "") if previous_action_payload else ""
        )
        previous_agent_summary = (
            previous_action_payload.get("previous_agent_summary", "") if previous_action_payload else ""
        )
        review_rejection_feedback = (
            previous_action_payload.get("review_rejection_feedback", "") if previous_action_payload else ""
        )
        prepare_environment_failure = (
            previous_action_payload.get("prepare_environment_failure", "") if previous_action_payload else ""
        )

        console.info(
            f"Agent fixing conformance test for functionality "
            f"{render_context.conformance_tests_running_context.current_testing_frid} "
            f"in module {render_context.conformance_tests_running_context.current_testing_module_name}."
        )

        # Snapshot files to detect changes later
        _, implementation_snapshot = ImplementationCodeHelpers.fetch_existing_files(render_context.build_folder)
        conformance_test_folder = self._get_conformance_test_folder(render_context)
        conformance_snapshot = self._snapshot_folder(conformance_test_folder)

        # Build context
        specifications = self._build_specifications_text(render_context)
        acceptance_tests = self._build_acceptance_tests_text(render_context)

        # Only provide code editing tools (no test/review tools)
        tools = {
            "write_file": write_file,
            "read_file": read_file,
            "list_files": list_files,
            "ls_files": ls_files,
            "grep": grep,
        }

        # Save test output to temp file
        test_output_file = self._save_test_output_to_file(previous_conformance_tests_issue)
        linked_resource_paths = self._get_linked_resource_paths(render_context)

        task_params = {
            "specifications": specifications,
            "test_output_file": test_output_file,
            "linked_resource_paths": linked_resource_paths,
            "acceptance_tests": acceptance_tests,
            "build_folder": render_context.build_folder,
            "conformance_tests_folder": conformance_test_folder,
            "module_name": render_context.module_name,
            "previous_agent_summary": previous_agent_summary,
            "review_rejection_feedback": review_rejection_feedback,
            "prepare_environment_failure": prepare_environment_failure,
        }

        tool_executor = ToolExecutor(available_tools=tools)
        response = agent_runner.run("fix_conformance_tests", task_params, render_context, tool_executor)

        # Extract agent's summary message
        agent_summary = response.get("result", "") if response.get("status") == "completed" else ""

        # Store snapshots and summary for review/test actions
        combined_snapshot = dict(implementation_snapshot)
        for path, content in conformance_snapshot.items():
            combined_snapshot[f"conformance_tests/{path}"] = content

        # Check if code was modified
        _, current_impl_files = ImplementationCodeHelpers.fetch_existing_files(render_context.build_folder)
        current_test_files = self._snapshot_folder(conformance_test_folder)

        implementation_changed = current_impl_files != implementation_snapshot
        tests_changed = current_test_files != conformance_snapshot

        return self.FIX_READY_FOR_REVIEW, {
            "agent_summary": agent_summary,
            "file_snapshot": combined_snapshot,
            "specifications": specifications,
            "acceptance_tests": acceptance_tests,
            "previous_conformance_tests_issue": previous_conformance_tests_issue,
            "conformance_test_folder": conformance_test_folder,
            "implementation_changed": implementation_changed,
            "tests_changed": tests_changed,
        }

    def _get_conformance_test_folder(self, render_context: RenderContext) -> str:
        ctx = render_context.conformance_tests_running_context
        if render_context.module_name == ctx.current_testing_module_name:
            return ctx.get_current_conformance_test_folder_name()
        folder, _ = render_context.conformance_tests.get_source_conformance_test_folder_name(
            render_context.module_name,
            render_context.required_modules,
            ctx.current_testing_module_name,
            ctx.get_current_conformance_test_folder_name(),
        )
        return folder

    def _snapshot_folder(self, folder: str) -> dict[str, str]:
        if not os.path.exists(folder):
            return {}
        files = file_utils.list_all_text_files(folder)
        return file_utils.get_existing_files_content(folder, files)

    def _build_specifications_text(self, render_context: RenderContext) -> str:
        frid = render_context.frid_context.frid
        specifications, _ = plain_spec.get_specifications_for_frid(render_context.plain_source_tree, frid)

        parts = []
        if specifications.get(plain_spec.DEFINITIONS):
            parts.append(f"## Definitions\n{chr(10).join(specifications[plain_spec.DEFINITIONS])}")
        if specifications.get(plain_spec.NON_FUNCTIONAL_REQUIREMENTS):
            parts.append(
                f"## Non-Functional Requirements\n{chr(10).join(specifications[plain_spec.NON_FUNCTIONAL_REQUIREMENTS])}"
            )
        if specifications.get(plain_spec.TEST_REQUIREMENTS):
            parts.append(
                f"## Test Requirements\n{chr(10).join(specifications[plain_spec.TEST_REQUIREMENTS])}"
            )
        if specifications.get(plain_spec.FUNCTIONAL_REQUIREMENTS):
            parts.append(
                f"## Functional Requirements\n{chr(10).join(specifications[plain_spec.FUNCTIONAL_REQUIREMENTS])}"
            )

        return "\n\n".join(parts)

    def _build_acceptance_tests_text(self, render_context: RenderContext) -> str:
        acceptance_tests = render_context.conformance_tests_running_context.get_current_acceptance_tests()
        if not acceptance_tests:
            return ""
        return "\n".join(acceptance_tests)

    def _read_conformance_tests_script(self, render_context: RenderContext) -> str:
        script_path = os.path.normpath(render_context.conformance_tests_script)
        if not os.path.exists(script_path):
            return ""
        with open(script_path, "r", encoding="utf-8") as f:
            return f.read()

    def _save_test_output_to_file(self, test_output: str) -> str:
        """Save test output to a temp file and return the path."""
        if not test_output:
            return ""

        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", delete=False, suffix=".test_output") as f:
            f.write(test_output)
            return f.name

    def _get_linked_resource_paths(self, render_context: RenderContext) -> list[str]:
        """Get list of linked resource paths (not content) for the agent to read if needed."""
        linked_resources = render_context.frid_context.linked_resources
        if not linked_resources:
            return []
        return list(linked_resources.keys())
