import tempfile
from typing import Any

import plain_spec
from render_machine.actions.base_action import BaseAction
from render_machine.agent import agent_runner
from render_machine.agent.tool_executor import ToolExecutor
from render_machine.agent.tools import (
    MAX_INLINE_OUTPUT_LINES,
    grep,
    list_files,
    read_file,
    run_unit_tests,
    write_file,
)
from render_machine.render_context import RenderContext

FIX_UNIT_TESTS_TOOLS = {
    "run_unit_tests": run_unit_tests,
    "write_file": write_file,
    "read_file": read_file,
    "list_files": list_files,
    "grep": grep,
}


class AgentFixUnitTests(BaseAction):
    SUCCESSFUL_OUTCOME = "unit_tests_fix_generated"

    def execute(self, render_context: RenderContext, previous_action_payload: Any | None):
        test_output = previous_action_payload.get("previous_unittests_issue", "") if previous_action_payload else ""
        test_output = self._truncate_test_output(test_output, render_context)

        task_params = {
            "specifications": self._build_specifications_text(render_context),
            "test_output": test_output,
        }

        tool_executor = ToolExecutor(available_tools=FIX_UNIT_TESTS_TOOLS)
        agent_runner.run("fix_unit_tests", task_params, render_context, tool_executor)

        return self.SUCCESSFUL_OUTCOME, None

    def _truncate_test_output(self, test_output: str, render_context: RenderContext) -> str:
        if not test_output:
            return test_output

        lines = test_output.split("\n")
        if len(lines) <= MAX_INLINE_OUTPUT_LINES:
            return test_output

        # Create a temporary file for the full output
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", delete=False, suffix=".test_output") as f:
            f.write(test_output)
            temp_file_path = f.name

        truncated = "\n".join(lines[:MAX_INLINE_OUTPUT_LINES])
        return (
            f"Output truncated ({len(lines)} total lines). "
            f"Full output available at: {temp_file_path}\n"
            f'Use read_file with file_path="{temp_file_path}" and base="temp" to see the complete output.\n\n{truncated}'
        )

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
