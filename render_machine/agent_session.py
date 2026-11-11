"""Driving a server-side agent session (codeplain-api: /agent/start, /agent/continue) up to its submission.

The server runs the LLM turns and returns the tool calls each turn asks for; the client executes them (see
agent_tools) and posts the results back. An agent ends a piece of work with its task's submit tool. What the
submission means - accept it, answer it with test output, check it - is up to the caller, so run_until_submit stops
there and hands the submit call back, together with the results of the other calls made in the same response
(they still have to reach the server with the answer to the submission, if the session continues).
"""

from dataclasses import dataclass, field
from typing import Optional

from render_machine import agent_tools


@dataclass
class AgentRun:
    """Where a run of a session stopped."""

    # The server's last response.
    response: dict
    # The submit call the run stopped at; None if it stopped without one (see `status`).
    submit_call: Optional[dict] = None
    # Results of the other tool calls of the submit call's response, already executed.
    tool_results: list[dict] = field(default_factory=list)
    turns: int = 0

    @property
    def status(self) -> str:
        """submitted, completed (the session ended without a submission), failed, or out_of_turns."""
        if self.submit_call is not None:
            return "submitted"
        if self.response.get("status") == "tool_calls":
            return "out_of_turns"
        return str(self.response.get("status") or "failed")

    @property
    def session_ended(self) -> bool:
        """Whether the server ended the session with the submission (its last turn forced it)."""
        return bool(self.response.get("session_ended"))


def run_until_submit(
    render_context,
    session_id: str,
    response: dict,
    submit_tool: str,
    scope: agent_tools.Target,
    max_turns: int,
) -> AgentRun:
    """Execute the tool calls of `response` and continue the session with their results, turn after turn, until
    the agent calls `submit_tool`, the session stops asking for tool calls, or `max_turns` turns were executed."""
    api = render_context.codeplain_api
    frid, module_name = render_context.frid_context.frid, render_context.module_name
    turns = 0
    while response.get("status") == "tool_calls" and turns < max_turns:
        turns += 1
        calls = response["calls"]
        submit_call = next((call for call in calls if call["name"] == submit_tool), None)
        tool_results = agent_tools.execute_calls([call for call in calls if call is not submit_call], scope)
        if submit_call is not None:
            return AgentRun(response, submit_call, tool_results, turns)
        response = api.agent_continue(session_id, tool_results, frid, module_name, render_context.run_state)
    return AgentRun(response, turns=turns)


def submit_result(call_id: str, output: str, **extra) -> dict:
    """The tool result answering a submission."""
    return {"call_id": call_id, "output": output, **extra}
