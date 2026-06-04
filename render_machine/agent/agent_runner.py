from plain2code_console import console
from plain2code_state import RunState
from render_machine.agent.tool_executor import ToolExecutor
from render_machine.render_context import RenderContext

MAX_AGENT_TURNS = 100
MAX_REVIEWER_TURNS = 30


def run(
    task_type: str,
    task_params: dict,
    render_context: RenderContext,
    tool_executor: ToolExecutor | None = None,
    max_turns: int | None = None,
    keep_session_alive: bool = False,
) -> dict:
    """Run an agent task to completion.

    Args:
        task_type: The agent task type (e.g. "fix_unit_tests").
        task_params: Initial parameters for the task (spec, test output, etc.).
        render_context: The current render context.
        tool_executor: Optional custom tool executor. Uses default if not provided.
        max_turns: Optional maximum number of turns. Defaults to MAX_AGENT_TURNS.
        keep_session_alive: If True, keep the session alive after completion for continuation.

    Returns:
        The final response from the agent (status "completed" or "failed").
    """
    if tool_executor is None:
        tool_executor = ToolExecutor()

    if max_turns is None:
        max_turns = MAX_AGENT_TURNS

    response = render_context.codeplain_api.agent_start(
        task_type=task_type,
        task_params=task_params,
        run_state=render_context.run_state,
        module_name=render_context.module_name,
        frid=render_context.frid_context.frid if render_context.frid_context else "",
    )

    turn_count = 0
    while response.get("status") == "tool_calls" and turn_count < max_turns:
        turn_count += 1
        tool_results = tool_executor.execute_calls(response["calls"], render_context)

        response = render_context.codeplain_api.agent_continue(
            session_id=response["session_id"],
            tool_results=tool_results,
            run_state=render_context.run_state,
            keep_session_alive=keep_session_alive,  # Keep alive if this is a persistent session
        )

    if response.get("status") == "completed":
        console.info(f"Agent task '{task_type}' completed successfully.")
    elif response.get("status") == "failed":
        console.warning(f"Agent task '{task_type}' failed: {response.get('error', 'unknown error')}")
    else:
        console.warning(f"Agent task '{task_type}' ended with status: {response.get('status')}")

    return response


def continue_session(
    session_id: str,
    additional_context: str,
    render_context: RenderContext,
    tool_executor: ToolExecutor | None = None,
    max_turns: int | None = None,
) -> dict:
    """Continue an existing agent session with additional context.

    Args:
        session_id: The existing agent session ID to continue.
        additional_context: New information to add to the conversation (e.g., test results, feedback).
        render_context: The current render context.
        tool_executor: Optional custom tool executor. Uses default if not provided.
        max_turns: Optional maximum number of turns. Defaults to MAX_AGENT_TURNS.

    Returns:
        The final response from the agent (status "completed" or "failed").
    """
    if tool_executor is None:
        tool_executor = ToolExecutor()

    if max_turns is None:
        max_turns = MAX_AGENT_TURNS

    # Send the additional context as a "user message" by using empty tool results
    # with the context embedded as a special message
    response = render_context.codeplain_api.agent_continue_with_message(
        session_id=session_id,
        message=additional_context,
        run_state=render_context.run_state,
    )

    turn_count = 0
    while response.get("status") == "tool_calls" and turn_count < max_turns:
        turn_count += 1
        tool_results = tool_executor.execute_calls(response["calls"], render_context)

        response = render_context.codeplain_api.agent_continue(
            session_id=session_id,
            tool_results=tool_results,
            run_state=render_context.run_state,
            keep_session_alive=True,  # Keep session alive for future continuations
        )

    if response.get("status") == "completed":
        console.info(f"Agent session '{session_id}' completed successfully.")
    elif response.get("status") == "failed":
        console.warning(f"Agent session '{session_id}' failed: {response.get('error', 'unknown error')}")
    else:
        console.warning(f"Agent session '{session_id}' ended with status: {response.get('status')}")

    return response
