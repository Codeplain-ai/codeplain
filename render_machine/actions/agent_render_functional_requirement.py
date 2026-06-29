from typing import Any

import file_utils
import plain_spec
import render_machine.render_utils as render_utils
from memory_management import MemoryManager
from plain2code_console import console
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
    think,
    write_file,
    write_memory,
)
from render_machine.render_context import RenderContext

RENDER_FUNCTIONAL_REQUIREMENT_TOOLS = {
    "edit_file": edit_file,
    "write_file": write_file,
    "delete_file": delete_file,
    "read_file": read_file,
    "ls_files": ls_files,
    "grep": grep,
    "run_command": run_command,
    "think": think,
    "write_memory": write_memory,
}


class AgentRenderFunctionalRequirement(BaseAction):
    SUCCESSFUL_OUTCOME = "code_and_unit_tests_generated"

    def execute(self, render_context: RenderContext, _previous_action_payload: Any | None):
        render_utils.revert_changes_for_frid(render_context)

        msg = "-------------------------------------\n"
        msg += f"Module: {render_context.module_name}\n"
        msg += f"Rendering functionality {render_context.frid_context.frid}:\n"
        msg += f"{render_context.frid_context.functional_requirement_text}\n"
        msg += "-------------------------------------"
        console.info(msg)

        memory_folder = render_context.memory_manager.memory_folder
        task_params = {
            "specifications": self._build_specifications_text(render_context),
            "linked_resource_paths": self._get_linked_resource_paths(render_context),
            "include_unittests": render_context.should_run_unit_tests(),
            "build_folder": render_context.build_folder,
            "module_name": render_context.module_name,
            "memory_folder": memory_folder,
            "memory_file_names": MemoryManager.list_memory_files(memory_folder),
        }

        tool_executor = ToolExecutor(available_tools=RENDER_FUNCTIONAL_REQUIREMENT_TOOLS)
        agent_runner.run("render_functional_requirement", task_params, render_context, tool_executor)

        changed_files = self._detect_changed_files(render_context)
        render_context.frid_context.changed_files.update(changed_files)

        return self.SUCCESSFUL_OUTCOME, None

    def _detect_changed_files(self, render_context: RenderContext) -> set[str]:
        all_files = file_utils.list_all_text_files(render_context.build_folder)
        return set(all_files)

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
                    f"### Module: {current_module} (Implement this functional requirement):\n"
                    f"{current_module_func_reqs[-1]}"
                )
            else:
                # Only one functionality (the current one)
                sections.append(
                    f"### Module: {current_module} (Implement this functional requirement):\n"
                    f"{current_module_func_reqs[0]}"
                )

        return "\n\n".join(sections)

    def _get_linked_resource_paths(self, render_context: RenderContext) -> list[str]:
        """Get list of linked resource paths (not content) for the agent to read if needed."""
        linked_resources = render_context.frid_context.linked_resources
        if not linked_resources:
            return []
        return list(linked_resources.keys())
