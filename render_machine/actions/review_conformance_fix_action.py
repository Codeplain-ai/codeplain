import hashlib
import os
from typing import Any

import diff_utils
import file_utils
from memory_management import AGENT_MEMORY_SUBFOLDER, GLOBAL_MEMORY_SUBFOLDER, MemoryManager
from plain2code_console import console
from plain2code_trace import preview, trace
from render_machine.actions.base_action import BaseAction
from render_machine.agent import agent_runner
from render_machine.agent.tool_executor import ToolExecutor
from render_machine.agent.tools import grep, ls_files, read_file, report_progress
from render_machine.render_context import RenderContext


class ReviewConformanceFixAction(BaseAction):
    APPROVED = "fix_approved"
    REJECTED = "fix_rejected"

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

        diff_text = diff_utils.get_code_diff(current_files, original_files)
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
        }

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

        # Reviewer gets read-only tools
        reviewer_tools = {
            "report_progress": report_progress,
            "think": report_progress,  # alias for older servers
            "read_file": read_file,
            "ls_files": ls_files,
            "grep": grep,
        }
        reviewer_executor = ToolExecutor(available_tools=reviewer_tools)

        # Run the reviewer agent to completion (with lower turn limit for reviewers)
        response = agent_runner.run(
            "review_conformance_fix",
            review_task_params,
            render_context,
            reviewer_executor,
            max_turns=agent_runner.MAX_REVIEWER_TURNS,
        )

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
            ctx.reset_file_change_tracker()
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
        console.info("Reverting rejected changes...")
        ctx.revert_tracked_changes()
        result_text += "\nThe rejected changes have been reverted. Propose a new fix."
        if memory_feedback:
            result_text += f"\nMemory feedback from the reviewer: {memory_feedback}"
        return self.REJECTED, {
            "review_rejection_feedback": result_text,
        }

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
