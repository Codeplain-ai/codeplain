import logging
import time
import traceback
from typing import Callable

from plain2code_trace import preview, summarize_args, trace
from render_machine.agent import tools
from render_machine.render_context import RenderContext

logger = logging.getLogger(__name__)

ToolFunction = Callable[[dict, RenderContext], str]

# Mirrors the server's TEMPORARILY_DISABLED_TOOLS (agent/agent_llm.py in codeplain-api).
# The model can emit calls to tools that were never declared to it (Gemini does not
# hard-constrain function-call names), and servers without the corresponding fix forward
# such calls verbatim — so the block must also be enforced here, at the point of
# execution. Empty this set to re-enable.
TEMPORARILY_DISABLED_TOOLS = {"run_command"}

DEFAULT_TOOLS: dict[str, ToolFunction] = {
    "run_unit_tests": tools.run_unit_tests,
    "run_command": tools.run_command,
    "edit_file": tools.edit_file,
    "write_file": tools.write_file,
    "delete_file": tools.delete_file,
    "read_file": tools.read_file,
    "ls_files": tools.ls_files,
    "grep": tools.grep,
    "get_session_changes": tools.get_session_changes,
    "report_progress": tools.report_progress,
    # Compatibility alias: older server versions still offer the tool as "think".
    "think": tools.report_progress,
    "write_memory": tools.write_memory,
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

        if name in TEMPORARILY_DISABLED_TOOLS:
            trace("tool", name=name, error="temporarily disabled")
            return (
                f"Error: the tool '{name}' is temporarily disabled and was NOT executed. "
                f"Do not call it again — further calls will also be refused. Use run_unit_tests "
                f"to run the unit test suite, and read_file / grep / ls_files to investigate instead."
            )

        tool_fn = self._tools.get(name)
        if tool_fn is None:
            trace("tool", name=name, error="unknown tool")
            return f"Error: Unknown tool '{name}'"

        started_at = time.monotonic()
        try:
            output = tool_fn(args, render_context)
        except Exception as e:
            tb = traceback.format_exc()
            logger.error(f"Tool '{name}' crashed with args {args}:\n{tb}")
            trace(
                "tool",
                name=name,
                args=summarize_args(args),
                duration_s=time.monotonic() - started_at,
                crashed=f"{type(e).__name__}: {e}",
            )
            return f"Error: Tool '{name}' crashed: {type(e).__name__}: {e}\n\nStack trace:\n{tb}"

        trace(
            "tool",
            name=name,
            args=summarize_args(args),
            duration_s=time.monotonic() - started_at,
            result_chars=len(output) if isinstance(output, str) else None,
            result=preview(output, 200),
        )
        return output
