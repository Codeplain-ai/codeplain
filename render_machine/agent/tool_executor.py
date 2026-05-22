from typing import Callable

from render_machine.agent import tools
from render_machine.render_context import RenderContext

ToolFunction = Callable[[dict, RenderContext], str]

DEFAULT_TOOLS: dict[str, ToolFunction] = {
    "run_unit_tests": tools.run_unit_tests,
    "run_conformance_tests": tools.run_conformance_tests,
    "write_file": tools.write_file,
    "read_file": tools.read_file,
    "list_files": tools.list_files,
    "grep": tools.grep,
}


class ToolExecutor:

    def __init__(self, available_tools: dict[str, ToolFunction] | None = None):
        self._tools = available_tools if available_tools is not None else DEFAULT_TOOLS

    def execute_calls(self, calls: list[dict], render_context: RenderContext) -> list[dict]:
        results = []
        for call in calls:
            output = self._execute_single(call, render_context)
            results.append({"call_id": call["id"], "output": output})
        return results

    def _execute_single(self, call: dict, render_context: RenderContext) -> str:
        name = call["name"]
        args = call.get("args", {})

        tool_fn = self._tools.get(name)
        if tool_fn is None:
            return f"Error: Unknown tool '{name}'"

        return tool_fn(args, render_context)
