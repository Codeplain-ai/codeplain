import tempfile
from typing import Any

import file_utils
import plain_spec
from memory_management import MemoryManager
from render_machine.actions.base_action import BaseAction
from render_machine.agent import agent_runner
from render_machine.agent.tool_executor import ToolExecutor
from render_machine.agent.tools import (
    delete_file,
    edit_file,
    grep,
    ls_files,
    read_file,
    run_command,
    run_unit_tests,
    think,
    write_file,
    write_memory,
)
from render_machine.render_context import RenderContext

FIX_UNIT_TESTS_TOOLS = {
    "run_unit_tests": run_unit_tests,
    "run_command": run_command,
    "edit_file": edit_file,
    "write_file": write_file,
    "delete_file": delete_file,
    "read_file": read_file,
    "ls_files": ls_files,
    "grep": grep,
    "think": think,
    "write_memory": write_memory,
}


class AgentFixUnitTests(BaseAction):
    SUCCESSFUL_OUTCOME = "unit_tests_fix_generated"
    TESTS_PASSED_OUTCOME = "unit_tests_succeeded"

    def execute(self, render_context: RenderContext, previous_action_payload: Any | None):
        test_output = previous_action_payload.get("previous_unittests_issue", "") if previous_action_payload else ""
        test_output_file = self._save_test_output_to_file(test_output)
        linked_resource_paths = self._get_linked_resource_paths(render_context)

        task_params = {
            "specifications": self._build_specifications_text(render_context),
            "linked_resource_paths": linked_resource_paths,
            "test_output_file": test_output_file,
            "unit_tests_script_path": render_context.unittests_script or "",
            "unit_tests_script_content": file_utils.read_script_content(render_context.unittests_script),
            "build_folder": render_context.build_folder,
            "module_name": render_context.module_name,
        }
        memory_files_content = MemoryManager.fetch_agent_memory_files(render_context.memory_manager.memory_folder)
        if memory_files_content:
            task_params["memory_files_content"] = memory_files_content

        tool_executor = ToolExecutor(available_tools=FIX_UNIT_TESTS_TOOLS)
        response = agent_runner.run("fix_unit_tests", task_params, render_context, tool_executor)

        # Check if agent successfully ran tests and they passed
        if response.get("status") == "completed":
            result_text = response.get("result", "")
            # If the agent's final message indicates tests passed, we can skip running them again
            if "unit tests passed successfully" in result_text.lower() or "all tests passed" in result_text.lower():
                return self.TESTS_PASSED_OUTCOME, None

        return self.SUCCESSFUL_OUTCOME, None

    def _save_test_output_to_file(self, test_output: str) -> str:
        """Save test output to a temp file and return the path."""
        if not test_output:
            return ""

        # Always create a temp file for test output
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", delete=False, suffix=".test_output") as f:
            f.write(test_output)
            return f.name

    def _get_linked_resource_paths(self, render_context: RenderContext) -> list[str]:
        """Get list of linked resource paths (not content) for the agent to read if needed."""
        linked_resources = render_context.frid_context.linked_resources
        if not linked_resources:
            return []
        return list(linked_resources.keys())

    def _build_specifications_text(self, render_context: RenderContext) -> str:
        frid = render_context.frid_context.frid
        specifications, _ = plain_spec.get_specifications_for_frid(render_context.plain_source_tree, frid)

        parts = []
        if specifications.get(plain_spec.DEFINITIONS):
            parts.append(f"## Definitions\n{chr(10).join(specifications[plain_spec.DEFINITIONS])}")
        if specifications.get(plain_spec.NON_FUNCTIONAL_REQUIREMENTS):
            parts.append(
                f"## Non-Functional Requirements\n"
                f"{chr(10).join(specifications[plain_spec.NON_FUNCTIONAL_REQUIREMENTS])}"
            )

        # Build functional requirements section with all modules
        func_req_parts = self._build_functional_requirements_section(render_context)
        if func_req_parts:
            parts.append(func_req_parts)

        return "\n\n".join(parts)

    def _build_functional_requirements_section(self, render_context: RenderContext) -> str:
        """Build functional requirements section showing all modules and their functionalities."""
        # Get functionalities from required modules
        required_modules_functionalities = render_context.get_required_modules_functionalities()
        current_module = render_context.module_name

        # Get current module's functionalities from specifications
        frid = render_context.frid_context.frid
        specifications, _ = plain_spec.get_specifications_for_frid(render_context.plain_source_tree, frid)
        current_module_func_reqs = specifications.get(plain_spec.FUNCTIONAL_REQUIREMENTS, [])

        # If no functionalities at all, return empty
        if not required_modules_functionalities and not current_module_func_reqs:
            return ""

        sections = ["## Functional Requirements\n"]

        # First, add required modules (all already implemented)
        for module_name, func_list in required_modules_functionalities.items():
            sections.append(
                f"### Module: {module_name} (Already Implemented, for context):\n" f"{chr(10).join(func_list)}"
            )

        # Then, add current module functionalities
        if current_module_func_reqs:
            if len(current_module_func_reqs) > 1:
                # Split into implemented and current
                sections.append(
                    f"### Module: {current_module} (Already Implemented, for context):\n"
                    f"{chr(10).join(current_module_func_reqs[:-1])}\n"
                )
                sections.append(
                    f"### Module: {current_module} (Currently Being Implemented):\n" f"{current_module_func_reqs[-1]}"
                )
            else:
                # Only one functionality (the current one)
                sections.append(
                    f"### Module: {current_module} (Currently Being Implemented):\n" f"{current_module_func_reqs[0]}"
                )

        return "\n\n".join(sections)
