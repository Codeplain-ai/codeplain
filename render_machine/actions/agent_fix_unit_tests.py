import os
from typing import Any

import file_utils
import plain_spec
import render_machine.render_utils as render_utils
from plain2code_console import console
from render_machine.actions.base_action import BaseAction
from render_machine.implementation_code_helpers import ImplementationCodeHelpers
from render_machine.render_context import RenderContext

MAX_AGENT_TURNS = 60


class AgentFixUnitTests(BaseAction):
    SUCCESSFUL_OUTCOME = "unit_tests_fix_generated"

    def execute(self, render_context: RenderContext, previous_action_payload: Any | None):
        test_output = previous_action_payload.get("previous_unittests_issue", "") if previous_action_payload else ""

        existing_files, existing_files_content = ImplementationCodeHelpers.fetch_existing_files(
            render_context.build_folder
        )

        task_params = {
            "specifications": self._build_specifications_text(render_context),
            "existing_files": existing_files_content,
            "test_output": test_output,
        }

        response = render_context.codeplain_api.agent_start(
            task_type="fix_unit_tests",
            task_params=task_params,
            run_state=render_context.run_state,
        )

        turn_count = 0
        while response.get("status") == "tool_calls" and turn_count < MAX_AGENT_TURNS:
            turn_count += 1
            tool_results = self._execute_tool_calls(response["calls"], render_context)

            response = render_context.codeplain_api.agent_continue(
                session_id=response["session_id"],
                tool_results=tool_results,
                run_state=render_context.run_state,
            )

        if response.get("status") == "completed":
            console.info("Agent successfully fixed unit tests.")
        else:
            console.warning(f"Agent finished with status: {response.get('status')}")

        return self.SUCCESSFUL_OUTCOME, None

    def _execute_tool_calls(self, calls: list[dict], render_context: RenderContext) -> list[dict]:
        results = []
        for call in calls:
            output = self._execute_single_tool(call, render_context)
            results.append({"call_id": call["id"], "output": output})
        return results

    def _execute_single_tool(self, call: dict, render_context: RenderContext) -> str:
        name = call["name"]
        args = call["args"]

        if name == "run_unit_tests":
            return self._tool_run_unit_tests(render_context)
        elif name == "write_file":
            return self._tool_write_file(args, render_context)
        elif name == "read_file":
            return self._tool_read_file(args, render_context)
        elif name == "list_files":
            return self._tool_list_files(args, render_context)
        else:
            return f"Error: Unknown tool '{name}'"

    def _tool_run_unit_tests(self, render_context: RenderContext) -> str:
        unittests_script = os.path.normpath(render_context.unittests_script)
        exit_code, output, _ = render_utils.execute_script(
            unittests_script,
            [render_context.build_folder],
            render_context.verbose,
            "Unit Tests",
            timeout=render_context.test_script_timeout,
            stop_event=render_context.stop_event,
        )
        if exit_code == 0:
            return "All unit tests passed successfully."
        return f"Tests failed (exit code {exit_code}):\n{output}"

    def _tool_write_file(self, args: dict, render_context: RenderContext) -> str:
        file_path = args.get("file_path", "")
        content = args.get("content", "")
        full_path = os.path.join(render_context.build_folder, file_path)

        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, "w", encoding="utf-8") as f:
            f.write(content)
        return f"Successfully wrote {file_path}"

    def _tool_read_file(self, args: dict, render_context: RenderContext) -> str:
        file_path = args.get("file_path", "")
        full_path = os.path.join(render_context.build_folder, file_path)

        if not os.path.exists(full_path):
            return f"Error: File '{file_path}' not found"
        with open(full_path, "r", encoding="utf-8") as f:
            return f.read()

    def _tool_list_files(self, args: dict, render_context: RenderContext) -> str:
        directory_path = args.get("directory_path", "")
        full_path = os.path.join(render_context.build_folder, directory_path)

        if not os.path.exists(full_path):
            return f"Error: Directory '{directory_path}' not found"

        files = file_utils.list_all_text_files(full_path)
        if not files:
            return "No files found in directory."
        return "\n".join(files)

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
