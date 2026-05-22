from plain2code_console import console
from plain2code_state import RunState
from render_machine.agent.tool_executor import ToolExecutor
from render_machine.render_context import RenderContext

MAX_AGENT_TURNS = 60


def run(
    task_type: str,
    task_params: dict,
    render_context: RenderContext,
    tool_executor: ToolExecutor | None = None,
) -> dict:
    """Run an agent task to completion.

    Args:
        task_type: The agent task type (e.g. "fix_unit_tests").
        task_params: Initial parameters for the task (spec, test output, etc.).
        render_context: The current render context.
        tool_executor: Optional custom tool executor. Uses default if not provided.

    Returns:
        The final response from the agent (status "completed" or "failed").
    """
    if tool_executor is None:
        tool_executor = ToolExecutor()

    response = render_context.codeplain_api.agent_start(
        task_type=task_type,
        task_params=task_params,
        run_state=render_context.run_state,
    )

    turn_count = 0
    while response.get("status") == "tool_calls" and turn_count < MAX_AGENT_TURNS:
        turn_count += 1
        tool_results = tool_executor.execute_calls(response["calls"], render_context)

        response = render_context.codeplain_api.agent_continue(
            session_id=response["session_id"],
            tool_results=tool_results,
            run_state=render_context.run_state,
        )

    if response.get("status") == "completed":
        console.info(f"Agent task '{task_type}' completed successfully.")
    elif response.get("status") == "failed":
        console.warning(f"Agent task '{task_type}' failed: {response.get('error', 'unknown error')}")
    else:
        console.warning(f"Agent task '{task_type}' ended with status: {response.get('status')}")

    return response
