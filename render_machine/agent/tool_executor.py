import logging
import traceback
from typing import Callable

from render_machine.agent import tools
from render_machine.render_context import RenderContext

logger = logging.getLogger(__name__)

ToolFunction = Callable[[dict, RenderContext], str]

DEFAULT_TOOLS: dict[str, ToolFunction] = {
    "run_unit_tests": tools.run_unit_tests,
    "run_conformance_tests": tools.run_conformance_tests,
    "edit_file": tools.edit_file,
    "write_file": tools.write_file,
    "delete_file": tools.delete_file,
    "read_file": tools.read_file,
    "list_files": tools.list_files,
    "ls_files": tools.ls_files,
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

        try:
            return tool_fn(args, render_context)
        except Exception as e:
            tb = traceback.format_exc()
            logger.error(f"Tool '{name}' crashed with args {args}:\n{tb}")
            return f"Error: Tool '{name}' crashed: {type(e).__name__}: {e}\n\nStack trace:\n{tb}"
