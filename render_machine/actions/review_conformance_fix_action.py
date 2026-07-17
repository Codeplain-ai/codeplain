import hashlib
import os
from typing import Any

import diff_utils
import file_utils
import git_utils
from memory_management import AGENT_MEMORY_SUBFOLDER, GLOBAL_MEMORY_SUBFOLDER, MemoryManager
from plain2code_console import console
from plain2code_trace import preview, trace
from render_machine import render_utils
from render_machine.actions.base_action import BaseAction
from render_machine.agent import agent_runner
from render_machine.agent.tool_executor import ToolExecutor
from render_machine.agent.tools import grep, ls_files, read_file, report_progress
from render_machine.render_context import RenderContext

# A reviewer run that ends with status "failed" is an infrastructure failure (LLM
# timeout, network error), not a verdict — it is retried this many times before the
# loop is sent back to the fix agent with an explicit resubmit instruction. It must
# never be interpreted as a rejection: a rejection carries consequences (counts
# toward the reset threshold, records a ledger outcome) that a failed call has not
# earned.
MAX_REVIEW_RUN_ATTEMPTS = 3

# A rejection does not revert the fix — the fix agent corrects its own changes with
# the reviewer's feedback. But once this many consecutive reviews have rejected, the
# un-reverted tree is anchoring the agent on the rejected approach rather than
# preserving useful work: the tree is reset to the last approved state, the tests are
# re-run to get a fresh failing output, and a fresh session takes over.
MAX_CONSECUTIVE_REVIEW_REJECTIONS = 2


class ReviewConformanceFixAction(BaseAction):
    APPROVED = "fix_approved"
    REJECTED = "fix_rejected"
    # Repeated rejections: the working tree was reset to the last approved state, so
    # the environment must be re-prepared and the tests re-run (they will fail again
    # with the original issue, giving the fresh fix session an accurate failing
    # output instead of the stale passing one produced by the rejected fix).
    REJECTED_AND_RESET = "fix_rejected_and_reset"

    def execute(self, render_context: RenderContext, _previous_action_payload: Any | None):
        ctx = render_context.conformance_tests_running_context

        # A fix's integrity is reviewed only after it has already made the conformance
        # tests pass, so this action is reached via the test-run transition rather than
        # directly from the fix agent. The reviewer inputs are therefore read from the
        # context (stashed by the fix agent), not from the previous action's payload.
        review_context = (ctx.fix_review_context if ctx else None) or {}
        specifications = review_context.get("specifications", "")
        acceptance_tests = review_context.get("acceptance_tests", "")
        conformance_test_folder = review_context.get("conformance_test_folder", "")
        failing_test_specifications = review_context.get("failing_test_specifications", "")
        fix_summary = ctx.last_fix_summary if ctx else {}

        console.info("Reviewing conformance test fix...")

        # We are now consuming the pending review; clear it so subsequent passing
        # test runs (e.g. during regression) are not re-routed back into review.
        if ctx:
            ctx.pending_fix_review = False

        # Compute diff from the file change tracker (tracks originals before modification)
        if not ctx or not ctx.file_change_tracker:
            console.warning("No changes detected in fix. Approving by default.")
            if ctx:
                render_context.finalize_accepted_conformance_fix()
            return self.APPROVED, {"review_rejection_feedback": ""}

        diff_text = self._compute_cumulative_diff(ctx)
        if not diff_text:
            console.warning("No changes detected in fix. Approving by default.")
            ctx.reset_file_change_tracker()
            render_context.finalize_accepted_conformance_fix()
            return self.APPROVED, {"review_rejection_feedback": ""}

        diff_str = ""
        for file_path, file_diff in diff_text.items():
            diff_str += f"--- {file_path}\n{file_diff}\n\n"

        # Get test output file path from context (set by RunConformanceTests)
        test_output_file = render_context.script_execution_history.latest_conformance_test_output_path or ""
        prepare_environment_output_file = (
            render_context.script_execution_history.latest_testing_environment_output_path or ""
        )

        # Build explanation from structured fix summary
        explanation = ""
        if fix_summary:
            explanation = (
                f"Root cause: {fix_summary.get('root_cause', 'N/A')}\nChanges: {fix_summary.get('changes_made', 'N/A')}"
            )

        # Build task params for the reviewer agent
        review_task_params = {
            "specifications": specifications,
            "acceptance_tests": acceptance_tests,
            "test_output_file": test_output_file,
            "prepare_environment_output_file": prepare_environment_output_file,
            "diff": diff_str,
            "explanation": explanation,
            "conformance_tests_script_path": render_context.conformance_tests_script or "",
            "conformance_tests_script_content": file_utils.read_script_content(render_context.conformance_tests_script),
            "prepare_environment_script_path": render_context.prepare_environment_script or "",
            "prepare_environment_script_content": file_utils.read_script_content(
                render_context.prepare_environment_script
            ),
            "build_folder": render_context.build_folder,
            "conformance_tests_folder": conformance_test_folder,
            "module_name": render_context.module_name,
            # The reviewer judges the fix against the same context the fixer had: the
            # failing test's own spec (a regression fix targets a different FRID than
            # the one being implemented) and the harness time budget (so a fix that
            # bends the spec to fit it is recognized — and a genuinely impossible
            # budget is flagged rather than "fixed").
            "test_script_timeout_seconds": render_utils.effective_test_script_timeout(render_context),
        }
        if failing_test_specifications:
            review_task_params["failing_test_specifications"] = failing_test_specifications

        # Memory produced by the fix session is reviewed alongside the fix: it feeds
        # every future session (global scope: every module), so destructive or noisy
        # content compounds if it slips through.
        proposed_key_learning = str((fix_summary or {}).get("key_learning") or "").strip()
        if proposed_key_learning:
            learning_scope = str((fix_summary or {}).get("learning_scope", "module"))
            review_task_params["proposed_key_learning"] = f"[scope: {learning_scope}]\n{proposed_key_learning}"
        session_memory_notes = self._read_session_memory_notes(ctx)
        if session_memory_notes:
            review_task_params["session_memory_notes"] = session_memory_notes

        response = self._run_reviewer_with_retries(render_context, review_task_params)

        if response.get("status") == "failed":
            # The review never ran to a verdict. The fix stays in place (tests pass
            # with it); route back to the fix agent with an explicit instruction to
            # resubmit so the review can be attempted again. This is deliberately NOT
            # counted as a rejection and must not trigger any revert.
            self._mark_last_ledger_outcome(
                ctx,
                "integrity review could not run (infrastructure failure); the fix was not judged — resubmit it",
            )
            return self.REJECTED, {
                "review_rejection_feedback": (
                    "The integrity review could not be completed due to an infrastructure error "
                    f"({preview(str(response.get('error', 'unknown error')))}). Your fix was NOT judged and has "
                    "NOT been reverted — it is still in the working tree and the conformance tests pass with it. "
                    "Re-submit the same fix via submit_fix so the review can run again."
                ),
            }

        verdict = self._extract_review_verdict(response)
        approved = verdict["approved"]
        result_text = verdict["result_text"]
        key_learning_verdict = verdict["key_learning_verdict"]
        memory_notes_to_reject = verdict["memory_notes_to_reject"]
        memory_feedback = verdict["memory_feedback"]
        trace(
            "review",
            verdict="APPROVED" if approved else "REJECTED",
            structured=verdict["structured"],
            diff_files=len(diff_text),
            diff_chars=len(diff_str),
            feedback=preview(result_text) if not approved else None,
            key_learning_verdict=key_learning_verdict or None,
            memory_notes_rejected=len(memory_notes_to_reject) or None,
        )

        # Notes the reviewer flagged as violating the memory guidelines are removed
        # regardless of the fix verdict — bad memory must not survive to be re-fed to
        # future sessions.
        self._delete_rejected_memory_notes(ctx, memory_notes_to_reject, memory_feedback)

        if approved:
            console.info("[green]Review APPROVED[/green]")
            ctx.consecutive_review_rejections = 0
            # Approval advances the review anchor: the approved changes are committed
            # (build repo + every conformance-test repo the fix touched) in the same
            # event the tracker is cleared, so the git state and the diff baseline
            # move together and the next review starts from this approved state.
            tracked_paths = list(ctx.file_change_tracker.keys())
            ctx.reset_file_change_tracker()
            self._commit_approved_fix(render_context, tracked_paths)
            # The fix is confirmed (tests pass + review approved). Persist the durable
            # learning only if the reviewer also approved the learning itself — a good
            # fix can still carry a bad learning (e.g. one canonizing the workaround
            # instead of the constraint).
            if key_learning_verdict == "rejected":
                console.warning(
                    "Reviewer rejected the proposed key learning; not persisting it to memory."
                    + (f" Reason: {memory_feedback}" if memory_feedback else "")
                )
                trace("review", event="key-learning-rejected", reason=preview(memory_feedback))
            else:
                self._persist_key_learning(render_context, fix_summary)
            # Surviving session notes are now reviewed and accepted — stop tracking them.
            ctx.session_memory_notes = []
            # The fix is accepted and the tests already pass: release the fix agent
            # session and clear unresolved memory, the cleanup previously done by
            # RunConformanceTests on a passing run (now deferred until acceptance).
            render_context.finalize_accepted_conformance_fix()
            return self.APPROVED, {"review_rejection_feedback": ""}

        console.warning(f"[yellow]Review REJECTED[/yellow]: {result_text}")
        if memory_feedback:
            result_text += f"\nMemory feedback from the reviewer: {memory_feedback}"

        ctx.consecutive_review_rejections += 1
        if ctx.consecutive_review_rejections >= MAX_CONSECUTIVE_REVIEW_REJECTIONS:
            return self._reset_to_approved_state(render_context, ctx, result_text)

        # The rejected changes are deliberately NOT reverted: the reviewer's rejections
        # itemize what must go and what is sound, and the fix agent — which has the
        # full context of its own changes — corrects them in place. Reverting here
        # destroyed valid parts of fixes (and desynced the tree from environment state
        # such as installed build artifacts). The tracker keeps accumulating, so the
        # next review sees the cumulative diff since the last approved state and the
        # flagged changes cannot slip past it unseen.
        result_text += (
            "\nYour changes have NOT been reverted — they are still in the working tree, and the conformance "
            "tests currently pass with them. Address the reviewer's feedback directly: rework or remove the "
            "parts the reviewer flagged (keep any parts the reviewer explicitly endorsed), then submit the "
            "corrected fix via submit_fix."
        )
        return self.REJECTED, {
            "review_rejection_feedback": result_text,
        }

    @staticmethod
    def _compute_cumulative_diff(ctx) -> dict:
        """Compute the cumulative diff (last approved state -> working tree) per file.

        Derived from the file change tracker, whose originals are the state at the
        last approval (approvals are the only point the tracker is cleared), so the
        reviewer always judges everything pending since the last blessed state —
        including changes a previous review already rejected.
        """
        current_files = {}
        original_files = {}
        for absolute_path, original_content in ctx.file_change_tracker.items():
            relative_path = os.path.relpath(absolute_path)
            if original_content is not None:
                original_files[relative_path] = original_content
            if os.path.exists(absolute_path):
                with open(absolute_path, "r", encoding="utf-8") as f:
                    current_files[relative_path] = f.read()
            else:
                current_files[relative_path] = ""

        return diff_utils.get_code_diff(current_files, original_files)

    @staticmethod
    def _run_reviewer_with_retries(render_context: RenderContext, review_task_params: dict) -> dict:
        """Run the reviewer agent, retrying runs that end in an infrastructure failure.

        A run with status "failed" (LLM timeout, network error) is not a verdict —
        treating it as a rejection would penalize a fix the reviewer never judged
        (and, before the no-revert change, destroyed passing fixes outright).
        """
        reviewer_tools = {
            "report_progress": report_progress,
            "think": report_progress,  # alias for older servers
            "read_file": read_file,
            "ls_files": ls_files,
            "grep": grep,
        }
        reviewer_executor = ToolExecutor(available_tools=reviewer_tools)

        response: dict = {}
        for attempt in range(1, MAX_REVIEW_RUN_ATTEMPTS + 1):
            response = agent_runner.run(
                "review_conformance_fix",
                review_task_params,
                render_context,
                reviewer_executor,
                max_turns=agent_runner.MAX_REVIEWER_TURNS,
            )
            if response.get("status") != "failed":
                break
            console.warning(
                f"Integrity review run failed (attempt {attempt}/{MAX_REVIEW_RUN_ATTEMPTS}): "
                f"{response.get('error', 'unknown error')}"
            )
            trace("review", event="review-run-failed", attempt=attempt, error=preview(str(response.get("error", ""))))
        return response

    def _commit_approved_fix(self, render_context: RenderContext, tracked_paths: list) -> None:
        """Commit an approved fix to every repository it touched, in one event.

        The build folder and each per-module conformance-tests folder are separate git
        repositories, and a single fix routinely spans both (an implementation change
        plus the test that asserts on it) — during regression it can even touch
        another module's conformance repo. Committing all affected repos here, at the
        moment of approval, keeps the review anchor consistent: everything committed
        is approved, everything uncommitted is pending review. Committing only some of
        them would let later reviews mix blessed and unblessed work in one diff.
        """
        ctx = render_context.conformance_tests_running_context
        commit_message = git_utils.APPROVED_CONFORMANCE_FIX_COMMIT_MESSAGE.format(
            ctx.current_testing_frid, ctx.current_testing_module_name
        )

        candidate_repos = [os.path.abspath(render_context.build_folder)]
        conformance_root = os.path.abspath(render_context.conformance_tests.conformance_tests_folder)
        # Conformance repos are per module; derive the touched ones from the tracked
        # paths, plus the current testing module's repo for side effects (e.g. files
        # written by run_command) that were never routed through the tracked tools.
        conformance_module_folders = {
            os.path.abspath(
                render_context.conformance_tests.get_module_conformance_tests_folder(ctx.current_testing_module_name)
            )
        }
        for path in tracked_paths:
            absolute_path = os.path.abspath(path)
            if absolute_path.startswith(conformance_root + os.sep):
                module_dir = os.path.relpath(absolute_path, conformance_root).split(os.sep)[0]
                conformance_module_folders.add(os.path.join(conformance_root, module_dir))
        candidate_repos.extend(sorted(conformance_module_folders))

        committed_repos = []
        for repo_path in candidate_repos:
            try:
                if not git_utils.is_dirty(repo_path):
                    continue
                git_utils.add_all_files_and_commit(
                    repo_path,
                    commit_message,
                    render_context.module_name,
                    render_context.frid_context.frid,
                    render_context.run_state.render_id,
                )
                committed_repos.append(repo_path)
            except Exception as e:
                # A candidate that is not a git repository (or fails to commit) must
                # not fail the approval — the tracker was already advanced, and the
                # remaining repos should still be committed.
                console.warning(f"Could not commit approved fix in '{repo_path}': {e}")

        if os.path.abspath(render_context.build_folder) in committed_repos:
            # Postprocessing consults this: with the build folder committed here it
            # can be clean at FRID postprocessing even though the implementation DID
            # change during conformance fixing, and the "implementation updated"
            # outcome (which gates ambiguity analysis) must still fire.
            ctx.implementation_committed_on_fix_approval = True
        if committed_repos:
            trace(
                "review",
                event="approved-fix-committed",
                repos=[os.path.relpath(repo) for repo in committed_repos],
            )

    def _reset_to_approved_state(self, render_context: RenderContext, ctx, result_text: str):
        """Reset the working tree to the last approved state after repeated rejections.

        Consecutive rejections mean the current tree is anchoring the fix agent on the
        rejected approach. The tracked changes are reverted (the tracker's originals
        ARE the last approved state, since approvals clear it), the fix session is
        rotated, and a harness-authored handoff carries the final rejection feedback —
        the rotation means the feedback cannot be delivered as a session continuation.
        The returned outcome routes back through environment preparation and a test
        run, so the fresh session starts from a real failing output instead of the
        stale passing one produced by the rejected fix.
        """
        console.warning(
            f"{ctx.consecutive_review_rejections} consecutive review rejections — resetting the working tree "
            "to the last approved state and starting a fresh fix session."
        )
        trace(
            "review",
            event="reset-to-approved-state",
            consecutive_rejections=ctx.consecutive_review_rejections,
            reverted_files=len(ctx.file_change_tracker),
        )
        self._mark_last_ledger_outcome(
            ctx,
            "rejected by the integrity reviewer (repeated rejections — the working tree was then reset "
            f"to the last approved state): {preview(result_text)}",
        )
        ctx.revert_tracked_changes()
        ctx.consecutive_review_rejections = 0
        ctx.fix_handoffs.append(
            "HARNESS NOTE (not agent-authored): consecutive fixes were rejected by the integrity reviewer, "
            "so all changes since the last approved state have been reverted — the working tree is back to "
            "the last approved state and the conformance tests fail again with the original issue (see the "
            "fresh test output). The final rejection feedback was:\n"
            f"{result_text}\n"
            "Do not retry the rejected approaches (see the attempts ledger). Solve the failure with a fix "
            "that satisfies the engineering integrity guidelines."
        )
        render_context._cleanup_fix_agent_session()
        ctx.fix_agent_session_id = None
        ctx.fix_agent_pending_tool_call_id = None
        return self.REJECTED_AND_RESET, {"review_rejection_feedback": ""}

    @staticmethod
    def _mark_last_ledger_outcome(ctx, outcome: str) -> None:
        """Record an outcome on the latest ledger entry directly from the review.

        Used on paths where the outcome would otherwise be mislabeled by the fix
        agent's generic payload-based bookkeeping (or never recorded at all, when the
        rejection routes through a test re-run instead of straight back to fixing).
        """
        if not ctx or not ctx.fix_attempts_ledger:
            return
        last_attempt = ctx.fix_attempts_ledger[-1]
        if not last_attempt.get("outcome"):
            last_attempt["outcome"] = outcome

    @staticmethod
    def _extract_review_verdict(response: dict) -> dict:
        """Extract the reviewer's verdicts from its response.

        The reviewer normally delivers its verdict through the submit_review terminal
        tool (structured), falling back to "VERDICT:" string matching only when it was
        force-concluded on its final turn (tool calls are ignored there).
        """
        result_text = response.get("result", "")
        verdict_args = response.get("terminal_tool_args") or {}
        structured_verdict = str(verdict_args.get("verdict", "")).strip().upper()
        feedback = str(verdict_args.get("feedback", "")).strip()
        if structured_verdict:
            # For terminal-tool responses "result" is the JSON-encoded tool args, not
            # prose — use the feedback field (or a placeholder) as the rejection text.
            result_text = feedback or "The reviewer rejected the fix without detailed feedback."

        approved = (
            structured_verdict == "APPROVED" if structured_verdict else "VERDICT: APPROVED" in result_text.upper()
        )
        return {
            "approved": approved,
            "structured": bool(structured_verdict),
            "result_text": result_text,
            "key_learning_verdict": str(verdict_args.get("key_learning_verdict", "")).strip().lower(),
            "memory_notes_to_reject": verdict_args.get("memory_notes_to_reject") or [],
            "memory_feedback": str(verdict_args.get("memory_feedback", "")).strip(),
        }

    @staticmethod
    def _read_session_memory_notes(ctx) -> dict:
        """Read the content of memory notes written during this fix loop, by file name."""
        notes: dict[str, str] = {}
        for note_path in ctx.session_memory_notes if ctx else []:
            try:
                with open(note_path, "r", encoding="utf-8") as f:
                    notes[os.path.basename(note_path)] = f.read()
            except OSError:
                continue
        return notes

    @staticmethod
    def _delete_rejected_memory_notes(ctx, memory_notes_to_reject, memory_feedback: str) -> None:
        """Delete session memory notes the reviewer flagged as violating the memory guidelines.

        Memory is fed to every future session (global notes to every module), so a note
        that canonizes a destructive or brittle practice compounds with each render —
        flagged notes are removed even when the fix itself is approved or the review
        loop continues.
        """
        if not ctx or not memory_notes_to_reject:
            return
        rejected_names = {os.path.basename(str(name)) for name in memory_notes_to_reject}
        kept: list[str] = []
        for note_path in ctx.session_memory_notes:
            if os.path.basename(note_path) not in rejected_names:
                kept.append(note_path)
                continue
            try:
                os.remove(note_path)
                console.warning(
                    f"Deleted memory note '{os.path.basename(note_path)}' rejected by the reviewer."
                    + (f" Reason: {memory_feedback}" if memory_feedback else "")
                )
                trace("review", event="memory-note-deleted", note=os.path.basename(note_path))
            except OSError as e:
                console.warning(f"Could not delete rejected memory note '{note_path}': {e}")
                kept.append(note_path)
        ctx.session_memory_notes = kept

    def _persist_key_learning(self, render_context: RenderContext, fix_summary: dict) -> None:
        """Save the fixing agent's key_learning (from submit_fix) to the module's memory.

        Called only after a fix is confirmed (tests pass + review approved). The note is a
        free-form markdown file that future agents discover and read on demand. The file name
        is keyed by module, FRID, and a short hash of the learning, so re-recording the same
        learning overwrites rather than duplicates.

        A "module"-scoped learning goes to .memory/agent_memory (local to this module). A
        "global"-scoped learning goes to .memory/global_memory, from where sync_global_memories
        propagates a committed copy into every module so all modules' agents receive it.
        """
        key_learning = (fix_summary or {}).get("key_learning")
        key_learning = key_learning.strip() if isinstance(key_learning, str) else ""
        if not key_learning:
            return

        is_global = str(fix_summary.get("learning_scope", "module")).strip().lower() == "global"
        subfolder = GLOBAL_MEMORY_SUBFOLDER if is_global else AGENT_MEMORY_SUBFOLDER

        module = render_context.module_name
        frid = render_context.frid_context.frid
        digest = hashlib.sha1(key_learning.encode("utf-8")).hexdigest()[:8]
        file_name = f"{module}_{frid}_{digest}.md".replace("/", "_").replace(os.sep, "_")

        scope_label = "project-wide" if is_global else f"module {module}"
        content = (
            f"# Conformance fix learning\n\n"
            f"- Scope: {'global' if is_global else 'module'} (applies to {scope_label})\n"
            f"- Originating module: {module}\n"
            f"- Functionality (FRID): {frid}\n"
            f"- Root cause: {fix_summary.get('root_cause', 'N/A')}\n"
            f"- Fix: {fix_summary.get('changes_made', 'N/A')}\n\n"
            f"## Key learning\n\n{key_learning}\n"
        )

        try:
            path = MemoryManager.write_agent_memory_file(
                render_context.memory_manager.memory_folder, file_name, content, subfolder=subfolder
            )
            console.info(f"Recorded {'global' if is_global else 'module'} conformance fix learning to memory: {path}")
        except Exception as e:
            console.warning(f"Failed to record conformance fix learning to memory: {e}")
