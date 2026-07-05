import json
import os

from plain2code_console import console
from render_machine.agent.tool_executor import ToolExecutor
from render_machine.render_context import RenderContext

MAX_AGENT_TURNS = 100
MAX_REVIEWER_TURNS = 30

TERMINAL_TOOLS = {"submit_fix", "submit_review"}


def run(
    task_type: str,
    task_params: dict,
    render_context: RenderContext,
    tool_executor: ToolExecutor | None = None,
    max_turns: int | None = None,
    keep_session_alive: bool = False,
    escalated: bool = False,
) -> dict:
    """Run an agent task to completion.

    Args:
        task_type: The agent task type (e.g. "fix_unit_tests").
        task_params: Initial parameters for the task (spec, test output, etc.).
        render_context: The current render context.
        tool_executor: Optional custom tool executor. Uses default if not provided.
        max_turns: Optional maximum number of turns. Defaults to MAX_AGENT_TURNS.
        keep_session_alive: If True, keep the session alive after completion for continuation.
        escalated: If True, ask the server to run this session on the task's stronger
            escalation model. Used when previous sessions already failed at this work
            (e.g. a fresh fix session carrying handoffs).

    Returns:
        The final response from the agent (status "completed" or "failed").
    """
    if tool_executor is None:
        tool_executor = ToolExecutor()

    if max_turns is None:
        max_turns = MAX_AGENT_TURNS

    # The project root is the client process CWD — the directory the agent's file tools
    # (read_file / write_file / ls_files / grep / run_command) resolve relative paths
    # against (see tools._get_project_root). Pass it through so the system prompt can tell
    # the agent exactly where it is working from, which cuts down on wrong relative paths
    # in those tool calls. setdefault so an explicitly provided value is never clobbered.
    task_params.setdefault("project_root", os.getcwd())

    response = render_context.codeplain_api.agent_start(
        task_type=task_type,
        task_params=task_params,
        run_state=render_context.run_state,
        module_name=render_context.module_name,
        frid=render_context.frid_context.frid if render_context.frid_context else "",
        escalated=escalated,
    )

    turn_count = 0
    while response.get("status") == "tool_calls" and turn_count < max_turns:
        turn_count += 1

        terminal_result = _extract_terminal_tool(response["calls"])
        if terminal_result is not None:
            # Agent called a terminal tool — end the loop, use its args as structured result
            response = _build_terminal_response(terminal_result, response.get("session_id"))
            if not keep_session_alive:
                # The terminal tool call is never answered for one-shot sessions (e.g.
                # the reviewer), so tell the server to release the session instead of
                # leaving it to expire via TTL.
                _end_session_quietly(response.get("session_id"), render_context)
            break

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
    pending_tool_call_id: str | None = None,
) -> dict:
    """Continue an existing agent session with additional context.

    Args:
        session_id: The existing agent session ID to continue.
        additional_context: New information to add to the conversation (e.g., test results, feedback).
        render_context: The current render context.
        tool_executor: Optional custom tool executor. Uses default if not provided.
        max_turns: Optional maximum number of turns. Defaults to MAX_AGENT_TURNS.
        pending_tool_call_id: If the previous turn ended on an unanswered terminal tool
            call (e.g. submit_fix), the additional context is delivered as that call's
            tool result instead of a new user message. This keeps the agent's tool loop
            intact so Gemini's prompt cache (and thought signatures) survive across fix
            attempts, instead of being invalidated by a new user turn.

    Returns:
        The final response from the agent (status "completed" or "failed").
    """
    if tool_executor is None:
        tool_executor = ToolExecutor()

    if max_turns is None:
        max_turns = MAX_AGENT_TURNS

    if pending_tool_call_id:
        # Answer the open terminal tool call with the feedback as its result. This
        # continues the same tool loop (no new user turn), preserving prompt caching.
        response = render_context.codeplain_api.agent_continue(
            session_id=session_id,
            tool_results=[{"call_id": pending_tool_call_id, "output": additional_context}],
            run_state=render_context.run_state,
            keep_session_alive=True,
        )
    else:
        # No open tool call to answer — fall back to adding the context as a user message.
        response = render_context.codeplain_api.agent_continue_with_message(
            session_id=session_id,
            message=additional_context,
            run_state=render_context.run_state,
        )

    turn_count = 0
    while response.get("status") == "tool_calls" and turn_count < max_turns:
        turn_count += 1

        terminal_result = _extract_terminal_tool(response["calls"])
        if terminal_result is not None:
            response = _build_terminal_response(terminal_result, session_id)
            break

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


def _end_session_quietly(session_id: str | None, render_context: RenderContext) -> None:
    """Best-effort release of a server-side session; failures are only logged."""
    if not session_id:
        return
    try:
        render_context.codeplain_api.agent_end_session(session_id, render_context.run_state)
    except Exception as e:
        console.warning(f"Could not end agent session '{session_id}': {e}")


def _build_terminal_response(terminal_result: dict, session_id: str | None) -> dict:
    """Build the completion response for a terminal tool call.

    Includes the terminal call's id so the caller can later answer it with a tool
    result (keeping the tool loop — and prompt cache — intact across continuations).
    """
    return {
        "status": "completed",
        "result": json.dumps(terminal_result["args"]),
        "session_id": session_id,
        "terminal_tool": terminal_result["name"],
        "terminal_tool_args": terminal_result["args"],
        "terminal_tool_call_id": terminal_result.get("id"),
    }


def _extract_terminal_tool(calls: list[dict]) -> dict | None:
    """Check if any tool call is a terminal tool (e.g. submit_fix).

    Returns the terminal call if found, or None if no terminal tool is present.
    """
    for call in calls:
        if call.get("name") in TERMINAL_TOOLS:
            return call
    return None
