"""The conversation between the conformance tests fixer and its reviewer.

Both are server-side agent sessions (tasks fix_conformance_tests and review_conformance_tests_fix in
codeplain-api) whose tool calls are executed here. The fixer edits the build folder and the conformance
tests folder and calls submit_fix; the reviewer reads the code and the diff of the fix and calls
submit_review. A rejection goes back to the fixer as the result of its submit_fix and it revises the fix;
an approval ends the conversation for now and the harness runs the tests. When they still fail, the same
two sessions continue with the new failure output. The sessions live as long as the conformance tests of
one functionality are being fixed (see ConformanceFixAgents).
"""

import difflib
import os
from dataclasses import dataclass
from http import HTTPStatus
from typing import Optional

import requests

from plain2code_console import console
from render_machine import agent_tools
from render_machine.render_types import AgentSessionState, ConformanceFixAgents

FIXER_TASK = "fix_conformance_tests"
REVIEWER_TASK = "review_conformance_tests_fix"
SUBMIT_FIX = "submit_fix"
SUBMIT_REVIEW = "submit_review"
CONFLICTING_REQUIREMENTS = "CONFLICTING_REQUIREMENTS"
FIXER_TOOLS = frozenset(
    {"read_file", "grep", "ls_files", "edit_file", "write_file", "delete_file", "run_conformance_tests"}
)
REVIEWER_TOOLS = frozenset({"read_file", "grep", "ls_files"})
# Upper bounds on LLM turns per fix and per review; the server forces the submission earlier (40 and 15).
MAX_FIXER_TURNS_PER_FIX = 50
MAX_REVIEWER_TURNS_PER_REVIEW = 20
# Rejections of one fix after which it is reverted and the fixer and reviewer start over in fresh sessions.
MAX_REJECTIONS = 5
# Fixer sessions one fix may start (after a revert, or when a session ends without submitting) before giving up.
MAX_FIXER_SESSIONS_PER_FIX = 3

TESTS_STILL_FAIL_MESSAGE = (
    "The reviewer approved your fix and it was applied, but the conformance tests still fail (output below). "
    "Files may have changed since you last read them: after an implementation change the unit tests are run, and "
    "fixing them may adjust the code."
)
# From this many failed runs in a row after approved fixes, the fixer is told its approach is not converging.
NOT_CONVERGING_AFTER_FAILED_RUNS = 3
NOT_CONVERGING_MESSAGE = (
    "\n\nThe tests have now failed {failed_runs} times in a row after approved fixes. If these failures have the same "
    "cause, the current approach is not converging: do not try another variant of it. Step back and change "
    "approach - question the assumption the attempts share (e.g. that an artifact, version, API, file or setting "
    "exists as you assume), satisfy the requirement in a different way, or, if it cannot be met as stated, say so "
    "in root_cause."
)
REJECTED_MESSAGE = "The reviewer rejected your fix."
EMPTY_FIX_MESSAGE = (
    "Your fix changes no files, so it was not sent to the reviewer: the conformance tests would fail again in the "
    "same way. Make the changes that fix the root cause, then call submit_fix again."
)
REVIEW_AFTER_TESTS_MESSAGE = (
    "The fix you approved was applied, but the conformance tests still fail (output below). The fixer has "
    "submitted a new fix; review it."
)
REVIEW_REVISION_MESSAGE = "The fixer revised the fix you rejected; review the revision."
NO_VERDICT_COMMENTS = (
    "The review could not be completed. Re-check that your fix addresses the root cause and follows the rules, "
    "then call submit_fix again."
)


@dataclass
class ApprovedFix:
    """A fix the reviewer approved: the fixer's submit_fix arguments and the files it changed, with their
    content from before the fix (None: the file did not exist)."""

    args: dict
    changed_files: dict[str, Optional[str]]


class ConformanceFixConversation:
    def __init__(self, render_context, agents: ConformanceFixAgents, fixer_params: dict, reviewer_params: dict):
        """`fixer_params` and `reviewer_params` are the task params a new session of each agent starts with,
        without the issue (which `fix` adds)."""
        self.render_context = render_context
        self.agents = agents
        self.fixer_params = fixer_params
        self.reviewer_params = reviewer_params
        self.build_folder = os.path.normpath(os.path.abspath(render_context.build_folder))
        self.fixer_workspace = agent_tools.AgentWorkspace(
            build_folder=self.build_folder,
            writable_folders=[
                self.build_folder,
                agents.conformance_tests_folder,
                *([agents.writable_module_tests_folder] if agents.writable_module_tests_folder else []),
            ],
            readable_folders=[agents.plain_modules_folder, *agents.readable_tests_folders],
            readable_log_paths=agents.readable_log_paths,
            tool_result_cache=agents.fixer_tool_result_cache,
            before_change=self._before_change,
            after_change=lambda _path: agents.reviewer_tool_result_cache.clear(),
            allowed_tools=FIXER_TOOLS,
            render_context=render_context,
            conformance_tests_folder=agents.conformance_tests_folder,
            prepared_signature=self._prepared_signature(render_context),
        )
        self.reviewer_workspace = agent_tools.AgentWorkspace(
            build_folder=self.build_folder,
            writable_folders=[],
            readable_folders=[
                agents.plain_modules_folder,
                agents.conformance_tests_folder,
                *agents.readable_tests_folders,
            ],
            readable_log_paths=agents.readable_log_paths,
            tool_result_cache=agents.reviewer_tool_result_cache,
            allowed_tools=REVIEWER_TOOLS,
        )

    @staticmethod
    def _prepared_signature(render_context) -> Optional[tuple]:
        """The build folder as the test environment was last prepared for, when that is the build folder as it is
        now (no implementation change since the failing run): run_conformance_tests then needs no preparation."""
        ctx = render_context.conformance_tests_running_context
        if not getattr(render_context, "prepare_environment_script", None) or ctx.should_prepare_testing_environment:
            return None
        from render_machine.actions import prepare_testing_environment

        return prepare_testing_environment._build_folder_signature(render_context.build_folder)

    # ---------------------------------------------------------------------------------------------------------
    # The conversation
    # ---------------------------------------------------------------------------------------------------------

    def fix(self, issue: str, log_path: Optional[str]) -> Optional[ApprovedFix]:
        """Run the conversation until the reviewer approves a fix for `issue` (the raw conformance tests output).
        Returns None if no fix was approved within the session budget; its changes are then left in place."""
        agents = self.agents
        agents.snapshot, agents.rejections = {}, 0
        if log_path:
            agent_tools.register_log_path(log_path, self.fixer_workspace)
        # The tests ran (and maybe unit tests were fixed) since the agents last looked at the files.
        agents.clear_tool_result_caches()
        # The harness has just run the tests on the files as they are, so a run_conformance_tests before any change
        # would only repeat that run.
        self.fixer_workspace.last_test_run = (
            agent_tools.test_run_files(self.render_context, self.agents.conformance_tests_folder),
            {
                "output": "The conformance tests failed: this is the failing run whose output you were given."
                + agent_tools.full_log_pointer(log_path)
            },
        )

        if agents.approved_fix_tested:
            agents.failed_runs += 1
        response = None
        if agents.fixer.session_id is not None:
            console.info(f"Continuing the conformance tests fixer session {agents.fixer.session_id}.")
            output = TESTS_STILL_FAIL_MESSAGE + agent_tools.full_log_pointer(log_path)
            if agents.failed_runs >= NOT_CONVERGING_AFTER_FAILED_RUNS:
                output += NOT_CONVERGING_MESSAGE.format(failed_runs=agents.failed_runs)
            response = self._answer(agents.fixer, {"output": output, "test_output": issue})

        sessions_started = 0
        while True:
            if response is None:
                if sessions_started >= MAX_FIXER_SESSIONS_PER_FIX:
                    console.warning("The conformance tests fixer did not get a fix approved.")
                    return None
                sessions_started += 1
                response = self._start(agents.fixer, FIXER_TASK, self._fixer_task_params(issue, log_path))

            submission = self._drive(agents.fixer, response, self.fixer_workspace, SUBMIT_FIX, MAX_FIXER_TURNS_PER_FIX)
            if submission is None:
                # The session ended without a submission (handoff, failure, turn budget): both agents start over,
                # keeping the changes, with a digest of their sessions.
                agents.reset_sessions()
                response = None
                continue

            args = submission["args"]
            console.info(f"The conformance tests fixer submitted a fix: {args.get('changes_made', '')}")
            changed_files = self._changed_files()
            if not changed_files and args.get("failing_reason") != CONFLICTING_REQUIREMENTS:
                response = self._answer(agents.fixer, {"output": EMPTY_FIX_MESSAGE})
                continue
            verdict, comments, objected_files = self._review(args, changed_files, issue, log_path)
            if verdict == "approve":
                console.info(f"The reviewer approved the fix: {comments or ''}")
                agents.approved_fix_tested = True
                return ApprovedFix(args, changed_files)

            console.info(f"The reviewer rejected the fix: {comments}")
            agents.rejections += 1
            review: dict = {"comments": comments}
            if agents.rejections % MAX_REJECTIONS == 0:
                # The sessions go on (both keep their history); only the changes the reviewer objected to go.
                reverted = self._revert_objected(objected_files, changed_files)
                if reverted:
                    console.warning(
                        f"The reviewer rejected {agents.rejections} fixes in a row; reverting the changes it objected "
                        f"to: {', '.join(reverted)}."
                    )
                    review["reverted_files"] = reverted
            response = self._answer(agents.fixer, {"output": REJECTED_MESSAGE, "review": review})

    def _review(
        self, args: dict, changed_files: dict, issue: str, log_path: Optional[str]
    ) -> tuple[str, str, list[str]]:
        """The reviewer's verdict on the submitted fix: ("approve" | "reject", comments, objected files)."""
        agents = self.agents
        fix_submission = {
            "root_cause": args.get("root_cause", ""),
            "failing_reason": args.get("failing_reason", ""),
            "changes_made": args.get("changes_made", ""),
            "review_response": args.get("review_response", ""),
            "conflict_reason": args.get("conflict_reason", ""),
            "code_diff": self.diff(changed_files),
            "consecutive_rejections": agents.rejections,
        }
        for _ in range(2):
            response = None
            if agents.reviewer.session_id is not None:
                result: dict = {"fix_submission": fix_submission}
                if agents.approved_fix_tested:
                    result["output"] = REVIEW_AFTER_TESTS_MESSAGE + agent_tools.full_log_pointer(log_path)
                    result["test_output"] = issue
                else:
                    result["output"] = REVIEW_REVISION_MESSAGE
                response = self._answer(agents.reviewer, result)
            if response is None:
                params = {**self.reviewer_params, **self._issue_params(issue, log_path)}
                params["fix_submission"] = fix_submission
                response = self._start(agents.reviewer, REVIEWER_TASK, params)
            agents.approved_fix_tested = False

            submission = self._drive(
                agents.reviewer, response, self.reviewer_workspace, SUBMIT_REVIEW, MAX_REVIEWER_TURNS_PER_REVIEW
            )
            if submission is None:
                agents.reviewer.reset()
                continue
            if response_ended(submission):
                # Its last turn: the verdict counts, and the next review starts a new session with a digest.
                agents.reviewer.reset()
            review_args = submission["args"]
            verdict = "approve" if review_args.get("verdict") == "approve" else "reject"
            return verdict, review_args.get("comments") or "", list(review_args.get("objected_files") or [])
        console.warning("The reviewer did not reach a verdict.")
        return "reject", NO_VERDICT_COMMENTS, []

    # ---------------------------------------------------------------------------------------------------------
    # Sessions
    # ---------------------------------------------------------------------------------------------------------

    def _start(self, state: AgentSessionState, task_type: str, task_params: dict) -> dict:
        if state.previous_session_id:
            task_params = {**task_params, "previous_session_id": state.previous_session_id}
        api = self.render_context.codeplain_api
        response = api.agent_start(
            task_type,
            task_params,
            self.render_context.frid_context.frid,
            self.render_context.module_name,
            self.render_context.run_state,
        )
        state.session_id = response["session_id"]
        console.info(f"Started the {task_type} agent session {state.session_id}.")
        return response

    def _answer(self, state: AgentSessionState, result: dict) -> Optional[dict]:
        """Answer the session's pending submit call with `result` and run its next turn; None when the session
        cannot be continued any more (expired or ended), in which case a new one is to be started."""
        tool_results = [*state.pending_tool_results, {"call_id": state.pending_call_id, **result}]
        state.pending_call_id, state.pending_tool_results = None, []
        return self._continue(state, tool_results)

    def _continue(self, state: AgentSessionState, tool_results: list[dict]) -> Optional[dict]:
        assert state.session_id is not None
        try:
            return self.render_context.codeplain_api.agent_continue(
                state.session_id,
                tool_results,
                self.render_context.frid_context.frid,
                self.render_context.module_name,
                self.render_context.run_state,
            )
        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response is not None else None
            if status not in (HTTPStatus.NOT_FOUND, HTTPStatus.BAD_REQUEST):
                raise
            console.warning(f"Agent session {state.session_id} can no longer be continued; starting a new one.")
            state.reset()
            return None

    def _drive(
        self, state: AgentSessionState, response: dict, workspace, submit_name: str, max_turns: int
    ) -> Optional[dict]:
        """Execute the session's tool calls until it calls `submit_name`, which is returned (left pending, to be
        answered by the next _answer). None if the session ended or used up `max_turns` without it."""
        turns = 0
        while response.get("status") == "tool_calls" and turns < max_turns:
            turns += 1
            calls = response["calls"]
            submission = next((call for call in calls if call["name"] == submit_name), None)
            results = agent_tools.execute_calls([call for call in calls if call is not submission], workspace)
            if submission is not None:
                state.pending_call_id, state.pending_tool_results = submission["id"], results
                return {**submission, "session_ended": bool(response.get("session_ended"))}
            next_response = self._continue(state, results)
            if next_response is None:
                return None
            response = next_response
        if response.get("status") == "failed":
            console.warning(f"Agent session {state.session_id} failed: {response.get('error', 'unknown error')}")
        elif response.get("status") == "tool_calls":
            console.warning(f"Agent session {state.session_id} used {max_turns} turns without submitting.")
        return None

    # ---------------------------------------------------------------------------------------------------------
    # Files
    # ---------------------------------------------------------------------------------------------------------

    def _before_change(self, full_path: str) -> None:
        if full_path not in self.agents.snapshot:
            self.agents.snapshot[full_path] = _read(full_path)

    def _changed_files(self) -> dict[str, Optional[str]]:
        """Files the current fix changed, with their content from before it."""
        return {path: before for path, before in self.agents.snapshot.items() if _read(path) != before}

    def _revert_objected(self, objected_files: list[str], changed_files: dict[str, Optional[str]]) -> list[str]:
        """Restore the changed files the reviewer objected to (named as in the diff, or by full path) to their
        content from before the fix; returns their names as the diff shows them."""
        objected = {name.strip().removeprefix("a/").removeprefix("b/") for name in objected_files}
        reverted = []
        for path, before in sorted(changed_files.items()):
            name = self.display_name(path)
            if name not in objected and path not in objected:
                continue
            if before is None:
                if os.path.isfile(path):
                    os.remove(path)
            else:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "w", encoding="utf-8") as f:
                    f.write(before)
            reverted.append(name)
        return reverted

    def display_name(self, full_path: str) -> str:
        """Build-folder files by their relative path (as the agents address them), others by their full path."""
        if full_path.startswith(self.build_folder + os.sep):
            return os.path.relpath(full_path, self.build_folder)
        return full_path

    def diff(self, changed_files: dict[str, Optional[str]]) -> dict[str, str]:
        diffs = {}
        for path, before in sorted(changed_files.items()):
            name = self.display_name(path)
            after = _read(path)
            if after is None:
                diffs[name] = "(file deleted)"
                continue
            diffs[name] = "".join(
                difflib.unified_diff(
                    (before or "").splitlines(keepends=True),
                    after.splitlines(keepends=True),
                    fromfile="/dev/null" if before is None else f"a/{name}",
                    tofile=f"b/{name}",
                )
            )
        return diffs

    # ---------------------------------------------------------------------------------------------------------
    # Task params
    # ---------------------------------------------------------------------------------------------------------

    def _issue_params(self, issue: str, log_path: Optional[str]) -> dict:
        params = {"conformance_tests_issue": issue}
        if log_path:
            params["conformance_tests_log_path"] = log_path
        return params

    def _fixer_task_params(self, issue: str, log_path: Optional[str]) -> dict:
        return {**self.fixer_params, **self._issue_params(issue, log_path)}


def response_ended(submission: dict) -> bool:
    return bool(submission.get("session_ended"))


def _read(path: str) -> Optional[str]:
    if not os.path.isfile(path):
        return None
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()
