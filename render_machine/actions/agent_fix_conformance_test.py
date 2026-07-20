from typing import Any

import file_utils
import plain_spec
import repo_map
from memory_management import MemoryManager
from plain2code_console import console
from plain2code_trace import preview, trace
from render_machine import fix_signals, render_utils
from render_machine.actions.base_action import BaseAction
from render_machine.agent import agent_runner
from render_machine.agent.tool_executor import ToolExecutor
from render_machine.agent.tools import (
    build_sandbox_contract,
    delete_file,
    edit_file,
    get_session_changes,
    grep,
    ls_files,
    read_file,
    report_progress,
    run_command,
    run_unit_tests,
    write_file,
    write_memory,
)
from render_machine.implementation_code_helpers import ImplementationCodeHelpers
from render_machine.render_context import RenderContext

# Most recent handoff notes injected into a fresh fix session. Each handoff is meant
# to be self-contained (a fresh agent summarizing everything tried so far, including
# what it inherited), so only the latest few are carried to keep the prompt bounded.
MAX_HANDOFFS_INJECTED = 3

# A submit_fix whose description matches an already-failed ledger entry is bounced
# back to the agent instead of spending a test/review cycle on it — but only once
# per attempt, so a determined resubmission (with its stated justification) still
# goes through rather than deadlocking the loop.
MAX_DUPLICATE_SUBMISSION_BOUNCES = 1


class AgentFixConformanceTest(BaseAction):
    FIX_APPLIED = "fix_applied"

    def execute(self, render_context: RenderContext, previous_action_payload: Any | None):
        ctx = render_context.conformance_tests_running_context

        # Gather feedback from previous iterations
        review_rejection_feedback = (
            previous_action_payload.get("review_rejection_feedback", "") if previous_action_payload else ""
        )
        # Detect whether we entered fixing because the environment preparation
        # (build/compile) step failed — either the first preparation after the tests
        # were generated or the re-preparation after a previous fix.
        # PrepareTestingEnvironment returns an ENVIRONMENT_ERROR payload, and the
        # state machine routes that here (MARK_TESTING_ENVIRONMENT_FAILED -->
        # CONFORMANCE_TEST_FAILED) without running the conformance tests. When that
        # happens the latest conformance test output is stale (the tests never ran),
        # so the agent must be pointed at the environment preparation output instead.
        error = previous_action_payload.get("error") if previous_action_payload else None
        prepare_environment_failed = bool(error and error.get("type") == "ENVIRONMENT_ERROR")

        # Fingerprint the current failure and compare with the previous run: an
        # unchanged signature after a fix is the strongest signal the agent has that
        # its approach isn't working. Computed before outcomes are recorded so the
        # ledger entry can carry it.
        failure_signature_notice = self._update_failure_signature(render_context, ctx, prepare_environment_failed)

        # Re-entering this action means the previous attempt (if any) did not resolve
        # the failure — record its outcome on the ledger before the next attempt.
        self._record_previous_attempt_outcome(ctx, review_rejection_feedback, prepare_environment_failed)

        trace(
            "fix-loop",
            event="enter",
            module=ctx.current_testing_module_name,
            testing_frid=ctx.current_testing_frid,
            fix_attempts=ctx.fix_attempts,
            session=ctx.fix_agent_session_id,
            handoffs=len(ctx.fix_handoffs),
            ledger_entries=len(ctx.fix_attempts_ledger),
            review_rejected=bool(review_rejection_feedback),
            prepare_environment_failed=prepare_environment_failed,
        )

        console.info(
            f"Agent fixing conformance test for functionality "
            f"{render_context.conformance_tests_running_context.current_testing_frid} "
            f"in module {render_context.conformance_tests_running_context.current_testing_module_name}."
        )

        # The file change tracker is owned exclusively by ReviewConformanceFixAction:
        # it is cleared there when a fix is APPROVED (and committed), and reverted
        # only when repeated rejections reset the tree to the last approved state. A
        # plain rejection clears nothing — the rejected changes stay in the working
        # tree for this agent to correct, and the tracker keeps accumulating so the
        # next review sees the cumulative diff since the last approved state. The
        # tracker must NOT be reset here, because a fix can loop back into fixing
        # several times (failed tests, rejections) before a review approves; resetting
        # would drop the original-file snapshots captured by earlier attempts.
        conformance_test_folder = self._get_conformance_test_folder(render_context)

        # Build context
        specifications = self._build_specifications_text(render_context)
        acceptance_tests = self._build_acceptance_tests_text(render_context)
        # During regression the failing test belongs to a different FRID (often a
        # different module) than the one being implemented — its own specification
        # must be provided explicitly, or the agent fixes a test whose defining spec
        # it has never seen.
        failing_test_specifications = self._build_failing_test_specifications_text(render_context)

        # Diffs of what this render step just produced. The conformance tests were
        # passing before this functionality was implemented, so these diffs point the
        # agent at the most likely source of the failure:
        #  - the implementation code added for the current functionality, and
        #  - the conformance tests just rendered for it,
        # each diffed against the previous functionality's commit.
        frid_implementation_diff = self._build_implementation_diff_text(render_context)
        frid_conformance_tests_diff = self._build_conformance_tests_diff_text(render_context)

        # Code editing tools plus diagnostics: run_command for targeted reproductions,
        # run_unit_tests so an implementation-code fix can be checked for unit-test
        # regressions BEFORE submitting (instead of a full cycle later), and
        # get_session_changes so the agent can self-review its cumulative diff.
        tools = {
            "edit_file": edit_file,
            "write_file": write_file,
            "delete_file": delete_file,
            "read_file": read_file,
            "ls_files": ls_files,
            "grep": grep,
            "run_command": run_command,
            "run_unit_tests": run_unit_tests,
            "get_session_changes": get_session_changes,
            "report_progress": report_progress,
            "think": report_progress,  # alias for older servers
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
                failing_test_specifications=failing_test_specifications,
            )
            self._add_optional_task_params(
                task_params,
                frid_implementation_diff=frid_implementation_diff,
                frid_conformance_tests_diff=frid_conformance_tests_diff,
                fix_handoffs=ctx.fix_handoffs,
                fix_attempts_ledger=ctx.fix_attempts_ledger,
            )
            response = agent_runner.run(
                "fix_conformance_tests",
                task_params,
                render_context,
                tool_executor,
                keep_session_alive=True,  # Keep session alive for future continuations
                # Handoffs mean a previous session already exhausted its turn budget
                # on this failure with the default model — escalate to the task's
                # stronger model instead of retrying with the same one.
                escalated=bool(ctx.fix_handoffs),
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
                failure_signature_notice=failure_signature_notice,
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
                    trace(
                        "fix-loop",
                        event="session-rotation",
                        old_session=session_id,
                        handoffs=len(ctx.fix_handoffs),
                        escalated=bool(ctx.fix_handoffs),
                        reason=preview(str(e)),
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
                        failing_test_specifications=failing_test_specifications,
                    )
                    self._add_optional_task_params(
                        task_params,
                        frid_implementation_diff=frid_implementation_diff,
                        frid_conformance_tests_diff=frid_conformance_tests_diff,
                        fix_handoffs=ctx.fix_handoffs,
                        fix_attempts_ledger=ctx.fix_attempts_ledger,
                    )
                    response = agent_runner.run(
                        "fix_conformance_tests",
                        task_params,
                        render_context,
                        tool_executor,
                        keep_session_alive=True,
                        escalated=bool(ctx.fix_handoffs),
                    )

                    # Store new session ID
                    if "session_id" in response:
                        render_context.conformance_tests_running_context.fix_agent_session_id = response["session_id"]
                else:
                    # Re-raise other errors
                    raise

        # A submission that repeats an already-failed ledger attempt is bounced back
        # to the agent before a test/review cycle is spent on it.
        response = self._bounce_duplicate_submissions(response, render_context, tool_executor, ctx)

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
                "key_learning": "",
                "learning_scope": "module",
            }

        # Store on context so it's available regardless of which path re-enters this action
        ctx.last_fix_summary = fix_summary

        trace(
            "fix-loop",
            event="fix-submitted",
            structured=bool(response.get("terminal_tool_args")),
            root_cause=preview(fix_summary.get("root_cause", "")),
            changes_made=preview(fix_summary.get("changes_made", "")),
            verification=preview(fix_summary.get("verification", "")),
            confidence=fix_summary.get("confidence"),
            files_modified=fix_summary.get("files_modified") or None,
        )

        # Every real submit_fix goes on the structured attempts ledger. Unlike the
        # free-text handoffs (only the most recent few are injected), the ledger is
        # complete, so approaches ruled out early cannot be silently retried by a
        # later session. The outcome field is filled in when the result is known.
        if response.get("terminal_tool_args"):
            ctx.fix_attempts_ledger.append(
                {
                    "root_cause": fix_summary.get("root_cause", ""),
                    "changes_made": fix_summary.get("changes_made", ""),
                    "verification": fix_summary.get("verification", ""),
                    "confidence": fix_summary.get("confidence", ""),
                    "outcome": "",
                }
            )

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
            "failing_test_specifications": failing_test_specifications,
        }

        return self.FIX_APPLIED, {
            "fix_summary": fix_summary,
            "specifications": specifications,
            "acceptance_tests": acceptance_tests,
            "conformance_test_folder": conformance_test_folder,
        }

    @staticmethod
    def _update_failure_signature(render_context: RenderContext, ctx, prepare_environment_failed: bool) -> str:
        """Fingerprint the current failing test output and compare with the last run.

        Returns a notice for the agent when consecutive fixes have not changed the
        failure signature (empty string otherwise). Skipped when the environment
        preparation failed — the conformance output is stale in that case.
        """
        if not ctx or prepare_environment_failed:
            return ""
        output_path = render_context.script_execution_history.latest_conformance_test_output_path
        if not output_path:
            return ""
        try:
            with open(output_path, "r", encoding="utf-8", errors="replace") as f:
                signature = fix_signals.compute_failure_signature(f.read())
        except OSError:
            return ""
        if signature is None:
            return ""

        previous_signature = ctx.last_failure_signature
        fix_was_applied = bool(ctx.fix_attempts_ledger)
        ctx.last_failure_signature = signature

        if not fix_was_applied or previous_signature != signature:
            ctx.failure_signature_streak = 0
            return ""

        ctx.failure_signature_streak += 1
        trace(
            "fix-loop",
            event="failure-signature-unchanged",
            streak=ctx.failure_signature_streak,
            signature=signature,
        )
        if ctx.failure_signature_streak == 1:
            return (
                "IMPORTANT: the failure signature is IDENTICAL to the run before your fix — the same "
                "tests fail with the same errors. Your change did not move the failure. First verify "
                "your edit is actually reaching the executed code (see the dead-edit axiom); if it is, "
                "your hypothesis is wrong — investigate a different component or layer."
            )
        return (
            f"CRITICAL: {ctx.failure_signature_streak} consecutive fixes have left the failure signature "
            "completely unchanged. Your current hypothesis class is exhausted — do NOT refine it further. "
            "You MUST change strategy: (1) verify your edits reach the executed code (dead-edit axiom), "
            "(2) re-read the failure output from scratch without your prior assumptions, and (3) pick a "
            "different component or layer to investigate. State your new hypothesis explicitly "
            "before editing anything."
        )

    def _bounce_duplicate_submissions(self, response: dict, render_context: RenderContext, tool_executor, ctx) -> dict:
        """Bounce a submit_fix that repeats an already-failed ledger attempt.

        The bounce answers the pending submit_fix call with an explanation and lets
        the session continue, instead of spending a full test/review cycle on an
        approach the ledger already records as failed. Bounced at most
        MAX_DUPLICATE_SUBMISSION_BOUNCES times per attempt so a deliberate
        resubmission (with its stated justification) still goes through.
        """
        bounces = 0
        while bounces < MAX_DUPLICATE_SUBMISSION_BOUNCES:
            fix_summary = response.get("terminal_tool_args")
            pending_call_id = response.get("terminal_tool_call_id")
            session_id = response.get("session_id") or (ctx.fix_agent_session_id if ctx else None)
            if not fix_summary or not pending_call_id or not session_id or not ctx:
                return response

            duplicate_index = fix_signals.find_duplicate_attempt(fix_summary, ctx.fix_attempts_ledger)
            if duplicate_index is None:
                return response

            bounces += 1
            prior = ctx.fix_attempts_ledger[duplicate_index]
            console.warning(
                f"Fix submission matches already-failed attempt #{duplicate_index + 1} "
                f"(outcome: {prior.get('outcome', 'unknown')}). Bouncing it back to the agent."
            )
            trace(
                "fix-loop",
                event="duplicate-submission-bounced",
                matches_attempt=duplicate_index + 1,
                prior_outcome=prior.get("outcome"),
                root_cause=preview(str(fix_summary.get("root_cause", ""))),
            )
            bounce_message = (
                f"SUBMISSION BOUNCED BEFORE TESTING: your submitted fix matches attempt "
                f"#{duplicate_index + 1} from the Previous Fix Attempts ledger, which already failed "
                f"(outcome: {prior.get('outcome', 'unknown')}).\n"
                f"That attempt claimed root cause: {prior.get('root_cause', 'n/a')}\n"
                f"And changed: {prior.get('changes_made', 'n/a')}\n\n"
                "Do not resubmit the same approach. Either state the NEW evidence that invalidates the "
                "recorded outcome and adjust the fix accordingly, or change your hypothesis class — "
                "investigate a different component or layer — and submit a genuinely different fix."
            )
            try:
                response = agent_runner.continue_session(
                    session_id=session_id,
                    additional_context=bounce_message,
                    render_context=render_context,
                    tool_executor=tool_executor,
                    pending_tool_call_id=pending_call_id,
                )
            except Exception as e:
                console.warning(f"Could not bounce duplicate submission ({e}); accepting it as-is.")
                return response
        return response

    @staticmethod
    def _record_previous_attempt_outcome(ctx, review_rejection_feedback: str, prepare_environment_failed: bool) -> None:
        """Fill in the outcome of the most recent ledger entry once it is known.

        This action re-runs only when the previous fix attempt failed, so at entry the
        latest outcome-less ledger entry can be resolved from the feedback that routed
        us back here.
        """
        if not ctx or not ctx.fix_attempts_ledger:
            return
        last_attempt = ctx.fix_attempts_ledger[-1]
        if last_attempt.get("outcome"):
            return
        if review_rejection_feedback:
            # Carry the reviewer's reasons onto the ledger: fresh sessions receive the
            # ledger (not the feedback payload), and "rejected" without the why is
            # exactly what lets a successor re-attempt the same violation.
            snippet = " ".join(review_rejection_feedback.split())
            if len(snippet) > 600:
                snippet = snippet[:600] + "…"
            last_attempt["outcome"] = f"rejected by the integrity reviewer: {snippet}"
        elif prepare_environment_failed:
            last_attempt["outcome"] = "build/compile failed after the fix; the tests never ran"
        elif ctx.failure_signature_streak >= 1:
            # The streak reaches fresh sessions via the ledger, so a post-rotation
            # agent knows these attempts did not even move the failure.
            last_attempt["outcome"] = (
                "conformance tests still failing — the failure signature was completely "
                "unchanged by this fix (same failing tests, same errors)"
            )
        else:
            last_attempt["outcome"] = "conformance tests still failing"
        trace(
            "fix-loop",
            event="attempt-outcome",
            attempt=len(ctx.fix_attempts_ledger),
            outcome=last_attempt["outcome"],
            root_cause=preview(last_attempt.get("root_cause", "")),
        )

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
        failing_test_specifications: str = "",
    ) -> dict:
        """Build the task params used to start a fresh fix-agent session.

        Shared by the first-attempt path and the session-rotation path so the two
        stay in sync.
        """
        task_params = {
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
            "memory_folder": render_context.memory_manager.memory_folder,
            "memory_file_names": MemoryManager.list_memory_files(render_context.memory_manager.memory_folder),
            "keep_session_alive": True,  # Mark this as a persistent session
            "test_script_timeout_seconds": render_utils.effective_test_script_timeout(render_context),
            "sandbox_contract": build_sandbox_contract(render_context),
        }
        if failing_test_specifications:
            task_params["failing_test_specifications"] = failing_test_specifications

        # Orientation seeds: codebase map (boosted by spec terms and the failing test
        # output, so the files implicated in the failure keep their outlines when the
        # map is over budget) plus the per-FRID implementation history.
        relevance_text = specifications + "\n" + repo_map.read_text_tail(test_output_file)
        repo_map_text = repo_map.build_repo_map_param(
            render_context,
            conformance_tests_folder=conformance_test_folder,
            relevance_text=relevance_text,
        )
        if repo_map_text:
            task_params["repo_map"] = repo_map_text
        code_brief = repo_map.read_code_brief(render_context.build_folder)
        if code_brief:
            task_params["code_brief"] = code_brief

        return task_params

    def _add_optional_task_params(
        self,
        task_params: dict,
        *,
        frid_implementation_diff: str,
        frid_conformance_tests_diff: str,
        fix_handoffs: list,
        fix_attempts_ledger: list | None = None,
    ) -> None:
        """Add the optional task params (only when present) shared by both start paths."""
        if frid_implementation_diff:
            task_params["frid_implementation_diff"] = frid_implementation_diff
        if frid_conformance_tests_diff:
            task_params["frid_conformance_tests_diff"] = frid_conformance_tests_diff
        if fix_handoffs:
            task_params["fix_handoffs"] = fix_handoffs[-MAX_HANDOFFS_INJECTED:]
        # The structured ledger is injected in full — it is compact (a few lines per
        # attempt), and truncating it is what allows failed approaches to be retried.
        if fix_attempts_ledger:
            task_params["attempts_ledger"] = list(fix_attempts_ledger)

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
            trace(
                "fix-loop",
                event="handoff-captured",
                session=ctx.fix_agent_session_id,
                handoff_number=len(ctx.fix_handoffs),
                handoff_chars=len(handoff),
                handoff=preview(handoff),
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

    def _build_failing_test_specifications_text(self, render_context: RenderContext) -> str:
        """Build the failing test's own specification when it differs from the implementing FRID.

        The main Specification section is centered on the FRID being implemented.
        During regression the failing test verifies a different FRID — often one of
        a required module, which appears there only as one-line functionality
        summaries. Without this section the agent (and the reviewer) judge a test
        whose defining spec they have never seen.
        """
        ctx = render_context.conformance_tests_running_context
        if ctx is None or ctx.current_testing_frid is None:
            return ""
        if (
            ctx.current_testing_module_name == render_context.module_name
            and ctx.current_testing_frid == render_context.frid_context.frid
        ):
            # The failing test IS the implementing FRID's — the main Specification
            # already centers on it.
            return ""

        parts = [
            f"Failing test: module `{ctx.current_testing_module_name}`, "
            f"functionality (FRID) {ctx.current_testing_frid}."
        ]

        if ctx.current_testing_module_name == render_context.module_name:
            # Same module, earlier FRID: the module's definitions and requirements are
            # already in the main Specification — add the failing functionality's text.
            failing_fr_text = self._get_failing_functional_requirement_text(ctx)
            if not failing_fr_text:
                return ""
            parts.append(f"### Functional requirement under test\n{failing_fr_text}")
            return "\n\n".join(parts)

        # Cross-module: pull the failing module's own spec sections from its plain
        # source (available on the requires chain), falling back to the functional
        # requirement text stored in its conformance_tests.json.
        specifications = self._get_failing_module_specifications(render_context, ctx)
        if specifications:
            if specifications.get(plain_spec.DEFINITIONS):
                parts.append(f"### Definitions\n{chr(10).join(specifications[plain_spec.DEFINITIONS])}")
            if specifications.get(plain_spec.NON_FUNCTIONAL_REQUIREMENTS):
                parts.append(
                    f"### Non-Functional Requirements\n"
                    f"{chr(10).join(specifications[plain_spec.NON_FUNCTIONAL_REQUIREMENTS])}"
                )
            if specifications.get(plain_spec.TEST_REQUIREMENTS):
                parts.append(f"### Test Requirements\n{chr(10).join(specifications[plain_spec.TEST_REQUIREMENTS])}")
            functional_requirements = specifications.get(plain_spec.FUNCTIONAL_REQUIREMENTS) or []
            if functional_requirements:
                parts.append(f"### Functional requirement under test\n{functional_requirements[-1]}")
        else:
            failing_fr_text = self._get_failing_functional_requirement_text(ctx)
            if failing_fr_text:
                parts.append(f"### Functional requirement under test\n{failing_fr_text}")

        return "\n\n".join(parts) if len(parts) > 1 else ""

    @staticmethod
    def _get_failing_module_specifications(render_context: RenderContext, ctx) -> dict | None:
        """Extract the failing module's spec sections for the failing FRID, if resolvable."""
        plain_module = getattr(render_context, "plain_module", None)
        candidates = []
        if plain_module is not None:
            candidates = plain_module.all_required_modules
        elif render_context.required_modules:
            candidates = render_context.required_modules
        failing_module = next(
            (module for module in candidates if module.module_name == ctx.current_testing_module_name), None
        )
        if failing_module is None:
            return None
        try:
            specifications, _ = plain_spec.get_specifications_for_frid(
                failing_module.plain_source, ctx.current_testing_frid
            )
            return specifications
        except Exception as e:
            console.warning(f"Could not extract the failing module's specifications for fixing context: {e}")
            return None

    @staticmethod
    def _get_failing_functional_requirement_text(ctx) -> str:
        """The failing FRID's functional requirement text, from the loaded test specs.

        ``current_testing_frid_specifications`` is a spec dict for same-module tests
        and the functional requirement string (from conformance_tests.json) for
        cross-module tests.
        """
        specs = ctx.current_testing_frid_specifications
        if isinstance(specs, dict):
            functional_requirements = specs.get(plain_spec.FUNCTIONAL_REQUIREMENTS) or []
            return functional_requirements[-1] if functional_requirements else ""
        if isinstance(specs, str):
            return specs
        return ""

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
        failure_signature_notice: str = "",
    ) -> str:
        """Build a message to continue the agent session with new test results and feedback."""
        parts = []

        # Review feedback. The review only runs after a fix makes the tests pass, so
        # on this path the tests currently PASS with the (kept) changes — the failure
        # sections below do not apply.
        if review_rejection_feedback:
            parts.append(
                f"The reviewer examined your fix and evaluated it according to the engineering integrity guidelines. The reviewer rejected your fix with this reviewer feedback:\n\n{review_rejection_feedback}\n"
            )
            parts.append(
                "Your changes were NOT reverted — they are still in the working tree, and the conformance "
                "tests currently pass with them. Correct the changes in place: rework or remove the parts "
                "the reviewer flagged, keep any parts the reviewer explicitly endorsed, and then submit the "
                "corrected fix.\n"
            )
            parts.append("\nNext steps:\n")
            parts.append("1. Thoroughly read the reviewer feedback above.\n")
            parts.append(
                "2. Re-read your cumulative changes (use get_session_changes) and identify which of them the "
                "reviewer flagged.\n"
            )
            parts.append("3. Correct the flagged changes in place so the fix addresses the root cause legitimately.\n")
            parts.append("4. Verify the corrected code by reading the edited files.\n")
            parts.append("5. When you are finished, submit the corrected fix using the submit_fix tool.\n")
            return "\n".join(parts)

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
            if failure_signature_notice:
                parts.append(f"{failure_signature_notice}\n")

        parts.append("\nNext steps:\n")
        parts.append("1. Thoroughly read the:\n")
        if prepare_environment_failed and prepare_environment_output_file:
            parts.append(f"  - The environment preparation output file at: {prepare_environment_output_file}\n")
        if not prepare_environment_failed and test_output_file:
            parts.append(f"  - The test output file at: {test_output_file}\n")
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
