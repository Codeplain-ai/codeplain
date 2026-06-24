from typing import Any

import file_utils
import plain_spec
from memory_management import MemoryManager
from plain2code_console import console
from render_machine.actions.base_action import BaseAction
from render_machine.agent import agent_runner
from render_machine.agent.tool_executor import ToolExecutor
from render_machine.agent.tools import (
    delete_file,
    edit_file,
    grep,
    ls_files,
    read_file,
    run_command,
    think,
    write_file,
    write_memory,
)
from render_machine.implementation_code_helpers import ImplementationCodeHelpers
from render_machine.render_context import RenderContext

# Most recent handoff notes injected into a fresh fix session. Each handoff is meant
# to be self-contained (a fresh agent summarizing everything tried so far, including
# what it inherited), so only the latest few are carried to keep the prompt bounded.
MAX_HANDOFFS_INJECTED = 3


class AgentFixConformanceTest(BaseAction):
    FIX_APPLIED = "fix_applied"

    def execute(self, render_context: RenderContext, previous_action_payload: Any | None):
        ctx = render_context.conformance_tests_running_context

        # Gather feedback from previous iterations
        review_rejection_feedback = (
            previous_action_payload.get("review_rejection_feedback", "") if previous_action_payload else ""
        )
        # Detect whether we re-entered fixing because the environment preparation
        # (build/compile) step failed after the previous fix. PrepareTestingEnvironment
        # returns an ENVIRONMENT_ERROR payload, and the state machine routes that back
        # here (CONFORMANCE_FIX_APPLIED --HANDLE_ERROR--> CONFORMANCE_TEST_FAILED) without
        # running the conformance tests. When that happens the latest conformance test
        # output is stale (the tests never ran), so the agent must be pointed at the
        # environment preparation output instead.
        error = previous_action_payload.get("error") if previous_action_payload else None
        prepare_environment_failed = bool(error and error.get("type") == "ENVIRONMENT_ERROR")

        console.info(
            f"Agent fixing conformance test for functionality "
            f"{render_context.conformance_tests_running_context.current_testing_frid} "
            f"in module {render_context.conformance_tests_running_context.current_testing_module_name}."
        )

        # The file change tracker is owned exclusively by ReviewConformanceFixAction:
        # it is cleared there once a fix cycle is reviewed (approved or reverted on
        # rejection). The tracker must NOT be reset here, because a fix can loop back
        # into fixing several times (e.g. when its changes still fail the tests) before
        # the tests pass and a review actually runs. Resetting here would drop the
        # original-file snapshots captured by earlier attempts, leaving the reviewer
        # with only the last attempt's diff instead of the cumulative diff since the
        # pre-fix baseline.
        conformance_test_folder = self._get_conformance_test_folder(render_context)

        # Build context
        specifications = self._build_specifications_text(render_context)
        acceptance_tests = self._build_acceptance_tests_text(render_context)

        # Diffs of what this render step just produced. The conformance tests were
        # passing before this functionality was implemented, so these diffs point the
        # agent at the most likely source of the failure:
        #  - the implementation code added for the current functionality, and
        #  - the conformance tests just rendered for it,
        # each diffed against the previous functionality's commit.
        frid_implementation_diff = self._build_implementation_diff_text(render_context)
        frid_conformance_tests_diff = self._build_conformance_tests_diff_text(render_context)

        # Code editing tools plus run_command for diagnostics (no full test/review tools).
        tools = {
            "edit_file": edit_file,
            "write_file": write_file,
            "delete_file": delete_file,
            "read_file": read_file,
            "ls_files": ls_files,
            "grep": grep,
            "run_command": run_command,
            "think": think,
            "write_memory": write_memory,
        }

        # Pick the output the agent should read based on which step failed:
        #  - environment preparation failed  -> the tests never ran, so the conformance
        #    test output is stale; show only the environment preparation output.
        #  - environment preparation succeeded -> the conformance tests just ran and
        #    failed; show only the (fresh) conformance test output.
        test_output_file = render_context.script_execution_history.latest_conformance_test_output_path or ""
        prepare_environment_output_file = (
            render_context.script_execution_history.latest_testing_environment_output_path or ""
        )
        if prepare_environment_failed:
            test_output_file = ""
        else:
            prepare_environment_output_file = ""
        linked_resource_paths = self._get_linked_resource_paths(render_context)
        memory_files_content = MemoryManager.fetch_agent_memory_files(render_context.memory_manager.memory_folder)

        tool_executor = ToolExecutor(available_tools=tools)

        # Check if this is a continuation of an existing fix session
        session_id = render_context.conformance_tests_running_context.fix_agent_session_id

        if session_id is None:
            # First attempt (or first attempt after session rotation) - start new agent session
            task_params = self._build_start_task_params(
                render_context,
                specifications=specifications,
                test_output_file=test_output_file,
                prepare_environment_output_file=prepare_environment_output_file,
                linked_resource_paths=linked_resource_paths,
                acceptance_tests=acceptance_tests,
                conformance_test_folder=conformance_test_folder,
            )
            self._add_optional_task_params(
                task_params,
                frid_implementation_diff=frid_implementation_diff,
                frid_conformance_tests_diff=frid_conformance_tests_diff,
                memory_files_content=memory_files_content,
                fix_handoffs=ctx.fix_handoffs,
            )
            response = agent_runner.run(
                "fix_conformance_tests",
                task_params,
                render_context,
                tool_executor,
                keep_session_alive=True,  # Keep session alive for future continuations
            )

            # Store session ID for future attempts
            if "session_id" in response:
                render_context.conformance_tests_running_context.fix_agent_session_id = response["session_id"]
        else:
            # Subsequent attempt - continue existing session with new information
            additional_context = self._build_continuation_message(
                test_output_file=test_output_file,
                review_rejection_feedback=review_rejection_feedback,
                prepare_environment_failed=prepare_environment_failed,
                prepare_environment_output_file=prepare_environment_output_file,
            )

            try:
                response = agent_runner.continue_session(
                    session_id=session_id,
                    additional_context=additional_context,
                    render_context=render_context,
                    tool_executor=tool_executor,
                    pending_tool_call_id=ctx.fix_agent_pending_tool_call_id,
                )
            except Exception as e:
                # If session not found (404) or expired, start a fresh session
                # This happens when the agent hits max turns or session expires
                if "404" in str(e) or "not found" in str(e).lower():
                    console.warning(
                        "Previous fix session expired or hit max turns. Starting fresh session with accumulated context."
                    )
                    render_context.conformance_tests_running_context.fix_agent_session_id = None

                    # Start new session with full context (includes prior handoffs)
                    task_params = self._build_start_task_params(
                        render_context,
                        specifications=specifications,
                        test_output_file=test_output_file,
                        prepare_environment_output_file=prepare_environment_output_file,
                        linked_resource_paths=linked_resource_paths,
                        acceptance_tests=acceptance_tests,
                        conformance_test_folder=conformance_test_folder,
                    )
                    self._add_optional_task_params(
                        task_params,
                        frid_implementation_diff=frid_implementation_diff,
                        frid_conformance_tests_diff=frid_conformance_tests_diff,
                        memory_files_content=memory_files_content,
                        fix_handoffs=ctx.fix_handoffs,
                    )
                    response = agent_runner.run(
                        "fix_conformance_tests",
                        task_params,
                        render_context,
                        tool_executor,
                        keep_session_alive=True,
                    )

                    # Store new session ID
                    if "session_id" in response:
                        render_context.conformance_tests_running_context.fix_agent_session_id = response["session_id"]
                else:
                    # Re-raise other errors
                    raise

        # If the agent ran out of turns without submitting a fix, the server force-
        # concludes the session and returns its final free-text response (no terminal
        # submit_fix tool call). Treat that response as an agent-authored handoff for
        # the next session, and end the exhausted session so the next fix cycle starts
        # a fresh agent that receives the handoff.
        if self._session_exhausted(response):
            self._capture_session_handoff(render_context, response)

        # Extract structured fix summary from submit_fix tool, or fall back to free-text result
        if response.get("terminal_tool_args"):
            fix_summary = response["terminal_tool_args"]
        else:
            fix_summary = {
                "root_cause": response.get("result", ""),
                "changes_made": "",
                "files_modified": [],
                "confidence": "unknown",
            }

        # Store on context so it's available regardless of which path re-enters this action
        ctx.last_fix_summary = fix_summary

        # Remember the (still unanswered) submit_fix tool call so the next attempt can
        # deliver its feedback as that call's tool result, preserving the tool loop and
        # Gemini's prompt cache instead of resetting it with a new user message.
        ctx.fix_agent_pending_tool_call_id = response.get("terminal_tool_call_id")

        # Mark that a fix is awaiting integrity review. The review is deferred until
        # the re-run of the conformance tests passes (see RunConformanceTests and the
        # "tests passed -> review" transition); a fix that does not even pass the
        # tests goes straight back to fixing without spending a review.
        ctx.pending_fix_review = True
        # Stash the reviewer's inputs on the context: the reviewer runs after the
        # tests (not directly after this action), so it cannot read them from the
        # action payload.
        ctx.fix_review_context = {
            "specifications": specifications,
            "acceptance_tests": acceptance_tests,
            "conformance_test_folder": conformance_test_folder,
        }

        return self.FIX_APPLIED, {
            "fix_summary": fix_summary,
            "specifications": specifications,
            "acceptance_tests": acceptance_tests,
            "conformance_test_folder": conformance_test_folder,
        }

    def _get_conformance_test_folder(self, render_context: RenderContext) -> str:
        ctx = render_context.conformance_tests_running_context
        if render_context.module_name == ctx.current_testing_module_name:
            return ctx.get_current_conformance_test_folder_name()
        folder, _ = render_context.conformance_tests.get_source_conformance_test_folder_name(
            render_context.module_name,
            render_context.required_modules,
            ctx.current_testing_module_name,
            ctx.get_current_conformance_test_folder_name(),
        )
        return folder

    def _build_specifications_text(self, render_context: RenderContext) -> str:
        frid = render_context.frid_context.frid
        specifications, _ = plain_spec.get_specifications_for_frid(render_context.plain_source_tree, frid)

        parts = []
        if specifications.get(plain_spec.DEFINITIONS):
            parts.append(f"## Definitions\n{chr(10).join(specifications[plain_spec.DEFINITIONS])}")
        if specifications.get(plain_spec.NON_FUNCTIONAL_REQUIREMENTS):
            parts.append(
                f"## Non-Functional Requirements\n"
                f"{chr(10).join(specifications[plain_spec.NON_FUNCTIONAL_REQUIREMENTS])}"
            )
        if specifications.get(plain_spec.TEST_REQUIREMENTS):
            parts.append(f"## Test Requirements\n" f"{chr(10).join(specifications[plain_spec.TEST_REQUIREMENTS])}")

        # Build functional requirements section with all modules
        func_req_parts = self._build_functional_requirements_section(render_context)
        if func_req_parts:
            parts.append(func_req_parts)

        return "\n\n".join(parts)

    def _build_functional_requirements_section(self, render_context: RenderContext) -> str:
        """Build functional requirements section showing all modules and their functionalities."""
        # Get functionalities from required modules
        required_modules_functionalities = render_context.get_required_modules_functionalities()
        current_module = render_context.module_name

        # Get current module's functionalities from specifications
        frid = render_context.frid_context.frid
        specifications, _ = plain_spec.get_specifications_for_frid(render_context.plain_source_tree, frid)
        current_module_func_reqs = specifications.get(plain_spec.FUNCTIONAL_REQUIREMENTS, [])

        # If no functionalities at all, return empty
        if not required_modules_functionalities and not current_module_func_reqs:
            return ""

        sections = ["## Functional Requirements\n"]

        # First, add required modules (all already implemented)
        for module_name, func_list in required_modules_functionalities.items():
            sections.append(
                f"### Module: {module_name} (Already Implemented, for context):\n" f"{chr(10).join(func_list)}"
            )

        # Then, add current module functionalities
        if current_module_func_reqs:
            if len(current_module_func_reqs) > 1:
                # Split into implemented and current
                sections.append(
                    f"### Module: {current_module} (Already Implemented, for context):\n"
                    f"{chr(10).join(current_module_func_reqs[:-1])}\n"
                )
                sections.append(
                    f"### Module: {current_module} (Currently Being Implemented):\n" f"{current_module_func_reqs[-1]}"
                )
            else:
                # Only one functionality (the current one)
                sections.append(
                    f"### Module: {current_module} (Currently Being Implemented):\n" f"{current_module_func_reqs[0]}"
                )

        return "\n\n".join(sections)

    def _build_start_task_params(
        self,
        render_context: RenderContext,
        *,
        specifications: str,
        test_output_file: str,
        prepare_environment_output_file: str,
        linked_resource_paths: list[str],
        acceptance_tests: str,
        conformance_test_folder: str,
    ) -> dict:
        """Build the task params used to start a fresh fix-agent session.

        Shared by the first-attempt path and the session-rotation path so the two
        stay in sync.
        """
        return {
            "specifications": specifications,
            "test_output_file": test_output_file,
            "prepare_environment_output_file": prepare_environment_output_file,
            "linked_resource_paths": linked_resource_paths,
            "acceptance_tests": acceptance_tests,
            "build_folder": render_context.build_folder,
            "conformance_tests_folder": conformance_test_folder,
            "conformance_tests_script_path": render_context.conformance_tests_script or "",
            "conformance_tests_script_content": file_utils.read_script_content(render_context.conformance_tests_script),
            "prepare_environment_script_path": render_context.prepare_environment_script or "",
            "prepare_environment_script_content": file_utils.read_script_content(
                render_context.prepare_environment_script
            ),
            "module_name": render_context.module_name,
            "keep_session_alive": True,  # Mark this as a persistent session
        }

    def _add_optional_task_params(
        self,
        task_params: dict,
        *,
        frid_implementation_diff: str,
        frid_conformance_tests_diff: str,
        memory_files_content,
        fix_handoffs: list,
    ) -> None:
        """Add the optional task params (only when present) shared by both start paths."""
        if frid_implementation_diff:
            task_params["frid_implementation_diff"] = frid_implementation_diff
        if frid_conformance_tests_diff:
            task_params["frid_conformance_tests_diff"] = frid_conformance_tests_diff
        if memory_files_content:
            task_params["memory_files_content"] = memory_files_content
        if fix_handoffs:
            task_params["fix_handoffs"] = fix_handoffs[-MAX_HANDOFFS_INJECTED:]

    @staticmethod
    def _session_exhausted(response: dict) -> bool:
        """True when the fix agent ran out of turns without submitting a fix.

        A normal attempt ends by calling submit_fix, which the client surfaces as
        ``terminal_tool_args``. When a session exhausts its turn budget the server
        force-concludes it and returns a plain ``completed`` response carrying only
        the agent's final free-text message (no terminal tool call).
        """
        return response.get("status") == "completed" and not response.get("terminal_tool_args")

    def _capture_session_handoff(self, render_context: RenderContext, response: dict) -> None:
        """Capture a turn-exhausted agent's final message as a handoff and end its session.

        The next fix cycle starts a fresh agent (session id cleared here) that receives
        the accumulated handoffs, so it continues from where its predecessor left off
        instead of starting blind.
        """
        ctx = render_context.conformance_tests_running_context
        handoff = (response.get("result") or "").strip()
        if handoff:
            ctx.fix_handoffs.append(handoff)
            console.warning(
                "Fix agent ran out of turns without resolving the failure. "
                "Captured a handoff for the next session and starting fresh."
            )
        # End the exhausted session so the next cycle does not try to continue a
        # turn-exhausted session (which would just be force-concluded again).
        render_context._cleanup_fix_agent_session()
        ctx.fix_agent_session_id = None
        ctx.fix_agent_pending_tool_call_id = None

    def _build_implementation_diff_text(self, render_context: RenderContext) -> str:
        """Format the diff of the implementation code added for the current functionality.

        This is the code change that implemented the current functionality (working
        tree vs the previous functionality's commit) — the prime suspect for a newly
        failing conformance test.
        """
        try:
            diff_by_file = ImplementationCodeHelpers.get_code_diff(
                render_context.build_folder,
                render_context.plain_source_tree,
                render_context.frid_context.frid,
            )
        except Exception as e:
            console.warning(f"Could not compute implementation code diff for fixing context: {e}")
            return ""
        return self._format_diff(diff_by_file)

    def _build_conformance_tests_diff_text(self, render_context: RenderContext) -> str:
        """Format the diff of the conformance tests just rendered for the current functionality.

        The conformance tests folder is committed per functionality, so diffing
        against the previous functionality's commit surfaces the tests just rendered
        for the current functionality.
        """
        try:
            conformance_tests_folder = render_context.conformance_tests.get_module_conformance_tests_folder(
                render_context.module_name
            )
            diff_by_file = ImplementationCodeHelpers.get_conformance_tests_diff(
                conformance_tests_folder,
                render_context.plain_source_tree,
                render_context.frid_context.frid,
            )
        except Exception as e:
            console.warning(f"Could not compute conformance tests diff for fixing context: {e}")
            return ""
        return self._format_diff(diff_by_file)

    @staticmethod
    def _format_diff(diff_by_file: dict) -> str:
        """Render a {file_name: unified_diff} mapping into a readable markdown block."""
        if not diff_by_file:
            return ""
        parts = []
        for file_name, file_diff in diff_by_file.items():
            parts.append(f"### {file_name}\n```diff\n{file_diff}\n```")
        return "\n\n".join(parts)

    def _build_acceptance_tests_text(self, render_context: RenderContext) -> str:
        acceptance_tests = render_context.conformance_tests_running_context.get_current_acceptance_tests()
        if not acceptance_tests:
            return ""
        return "\n".join(acceptance_tests)

    def _get_linked_resource_paths(self, render_context: RenderContext) -> list[str]:
        """Get list of linked resource paths (not content) for the agent to read if needed."""
        linked_resources = render_context.frid_context.linked_resources
        if not linked_resources:
            return []
        return list(linked_resources.keys())

    def _build_continuation_message(
        self,
        test_output_file: str,
        review_rejection_feedback: str,
        prepare_environment_failed: bool,
        prepare_environment_output_file: str,
    ) -> str:
        """Build a message to continue the agent session with new test results and feedback."""
        parts = []

        # Review feedback
        if review_rejection_feedback:
            parts.append(
                f"The reviewer examined your fix and evaluated it according to the engineering integrity guidelines. The reviewer rejected your fix with this reviewer feedback:\n\n{review_rejection_feedback}\n"
            )
            parts.append("Please address the reviewer's concerns and try again.\n")

        if prepare_environment_failed:
            # The build/compile step failed after the fix, so the conformance tests never
            # ran. Only the environment preparation output is relevant here; the previous
            # conformance test output is stale and is deliberately not referenced.
            if prepare_environment_output_file:
                parts.append(
                    "After your fix, the environment preparation (build/compile) step failed, so the "
                    "conformance tests could NOT be run. The environment preparation output was saved to a "
                    f"file which is available at: {prepare_environment_output_file}\n"
                )
            else:
                parts.append(
                    "After your fix, the environment preparation (build/compile) step failed, so the "
                    "conformance tests could NOT be run, and no environment preparation output file is available.\n"
                )
            parts.append(
                "Please fix the build/compilation issues so the environment can be prepared and the tests can run.\n"
            )
        else:
            # The environment preparation succeeded and the conformance tests just ran and failed.
            if test_output_file:
                parts.append(
                    f"Your fix was evaluated by running the conformance tests using the conformance tests script, but the tests still failed. "
                    f"The test script output was saved to a file which is available at: {test_output_file}\n\n"
                )
            else:
                parts.append(
                    "Your fix was evaluated by running the conformance tests using the conformance tests script, but the tests still failed, and no test output file is available.\n"
                )

        parts.append("\nNext steps:\n")
        parts.append("1. Thoroughly read the:\n")
        if prepare_environment_failed and prepare_environment_output_file:
            parts.append(f"  - The environment preparation output file at: {prepare_environment_output_file}\n")
        if not prepare_environment_failed and test_output_file:
            parts.append(f"  - The test output file at: {test_output_file}\n")
        if review_rejection_feedback:
            parts.append("  - The reviewer feedback\n")
        parts.append(
            "2. Locate and read the relevant test code and the implementation code that is being tested. Make sure you understand the root cause of the failure.\n"
        )
        parts.append(
            "3a. If you understand exactly what the issue is: Implement a fix that addresses the root cause.\n"
        )
        parts.append(
            "3b. If you do not exactly understand what the issue is: Make adjustments to the code that help you diagnose the issue (like additional logs for example).\n"
        )
        parts.append("4. Verify the fix or adjustments by reading the edited files to ensure the fix is correct.\n")
        parts.append(
            "5. When you are finished with implementing the fix or diagnostic adjustments run the tests again by using the submit_fix tool.\n"
        )

        return "\n".join(parts)
