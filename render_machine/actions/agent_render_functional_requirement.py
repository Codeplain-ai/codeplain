import os
from typing import Any

import file_utils
import plain_spec
import render_machine.render_utils as render_utils
from plain2code_console import console
from render_machine.actions.base_action import BaseAction
from render_machine.agent import agent_runner
from render_machine.agent.tool_executor import ToolExecutor
from render_machine.agent.tools import grep, list_files, read_file, write_file
from render_machine.render_context import RenderContext

RENDER_FUNCTIONAL_REQUIREMENT_TOOLS = {
    "write_file": write_file,
    "read_file": read_file,
    "list_files": list_files,
    "grep": grep,
}


class AgentRenderFunctionalRequirement(BaseAction):
    SUCCESSFUL_OUTCOME = "code_and_unit_tests_generated"

    def execute(self, render_context: RenderContext, _previous_action_payload: Any | None):
        render_utils.revert_changes_for_frid(render_context)

        if render_context.verbose:
            msg = "-------------------------------------\n"
            msg += f"Module: {render_context.module_name}\n"
            msg += f"Rendering functionality {render_context.frid_context.frid}:\n"
            msg += f"{render_context.frid_context.functional_requirement_text}\n"
            msg += "-------------------------------------"
            console.info(msg)

        task_params = {
            "specifications": self._build_specifications_text(render_context),
            "linked_resources": self._build_linked_resources_text(render_context),
            "include_unittests": render_context.should_run_unit_tests(),
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
                f"## Non-Functional Requirements\n{chr(10).join(specifications[plain_spec.NON_FUNCTIONAL_REQUIREMENTS])}"
            )
        if specifications.get(plain_spec.FUNCTIONAL_REQUIREMENTS):
            parts.append(
                f"## Functional Requirements\n{chr(10).join(specifications[plain_spec.FUNCTIONAL_REQUIREMENTS])}"
            )

        return "\n\n".join(parts)

    def _build_linked_resources_text(self, render_context: RenderContext) -> str:
        linked_resources = render_context.frid_context.linked_resources
        if not linked_resources:
            return ""

        parts = []
        for resource_path, resource_content in linked_resources.items():
            parts.append(f"### {resource_path}\n```\n{resource_content}\n```")

        return "\n\n".join(parts)
