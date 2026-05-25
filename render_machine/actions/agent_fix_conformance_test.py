import os
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
    prepare_environment,
    read_file,
    run_conformance_tests,
    write_file,
)
from render_machine.implementation_code_helpers import ImplementationCodeHelpers
from render_machine.render_context import RenderContext


class AgentFixConformanceTest(BaseAction):
    IMPLEMENTATION_CODE_NOT_UPDATED = "implementation_code_not_updated"
    IMPLEMENTATION_CODE_UPDATED = "implementation_code_updated"

    def execute(self, render_context: RenderContext, previous_action_payload: Any | None):
        previous_conformance_tests_issue = (
            previous_action_payload.get("previous_conformance_tests_issue", "") if previous_action_payload else ""
        )

        console.info(
            f"Agent fixing conformance test for functionality "
            f"{render_context.conformance_tests_running_context.current_testing_frid} "
            f"in module {render_context.conformance_tests_running_context.current_testing_module_name}."
        )

        # Snapshot both implementation and conformance test files
        _, implementation_snapshot = ImplementationCodeHelpers.fetch_existing_files(render_context.build_folder)
        conformance_test_folder = self._get_conformance_test_folder(render_context)
        conformance_snapshot = self._snapshot_folder(conformance_test_folder)

        # Combined snapshot for diff computation (prefix conformance test paths)
        combined_snapshot = dict(implementation_snapshot)
        for path, content in conformance_snapshot.items():
            combined_snapshot[f"conformance_tests/{path}"] = content

        # Build context for the review tool
        specifications = self._build_specifications_text(render_context)
        acceptance_tests = self._build_acceptance_tests_text(render_context)

        # Create the review tool with captured context
        conformance_tests_script_content = self._read_conformance_tests_script(render_context)
        submit_fix_for_review = create_submit_fix_for_review(
            file_snapshot=combined_snapshot,
            specifications=specifications,
            acceptance_tests=acceptance_tests,
            test_failure=previous_conformance_tests_issue,
            conformance_test_folder=conformance_test_folder,
            conformance_tests_script=conformance_tests_script_content,
        )

        tools = {
            "run_conformance_tests": run_conformance_tests,
            "prepare_environment": prepare_environment,
            "write_file": write_file,
            "read_file": read_file,
            "list_files": list_files,
            "grep": grep,
            "submit_fix_for_review": submit_fix_for_review,
        }

        task_params = {
            "specifications": specifications,
            "test_output": previous_conformance_tests_issue,
            "acceptance_tests": acceptance_tests,
            "conformance_tests_script": self._read_conformance_tests_script(render_context),
        }

        tool_executor = ToolExecutor(available_tools=tools)
        agent_runner.run("fix_conformance_tests", task_params, render_context, tool_executor)

        # Determine if implementation code was modified
        _, current_files = ImplementationCodeHelpers.fetch_existing_files(render_context.build_folder)
        implementation_changed = current_files != implementation_snapshot

        if implementation_changed:
            return self.IMPLEMENTATION_CODE_UPDATED, None
        return self.IMPLEMENTATION_CODE_NOT_UPDATED, None

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
