import os
from typing import Any

import file_utils
import plain_spec
from plain2code_console import console
from plain2code_exceptions import InternalClientError
from render_machine import agent_tools
from render_machine.actions.base_action import BaseAction
from render_machine.render_context import RenderContext

TASK_TYPE = "fix_unit_tests"
SUBMIT_FIX_TOOL = "submit_fix"
# Upper bound on LLM turns spent on one fix attempt; the server bounds the whole session.
MAX_AGENT_TURNS_PER_ATTEMPT = 40
# Seeding the first turn: the build folder's file list and the files changed for the FRID.
MAX_FILE_TREE_ENTRIES = 500
MAX_RELEVANT_FILES_CHARS = 60_000


class FixUnitTests(BaseAction):
    """Fix failing unit tests with a server-side agent whose session spans all attempts for the FRID.

    The first failure starts a session; the agent then drives read/grep/edit/run tool calls
    (executed here) until it calls submit_fix. The state machine re-runs the unit tests and,
    if they still fail, the next execution of this action answers that submit_fix call with
    the new failure output inside the same session, so earlier attempts stay in context.
    """

    SUCCESSFUL_OUTCOME = "unit_tests_fix_generated"

    def execute(self, render_context: RenderContext, previous_action_payload: Any | None):
        if not previous_action_payload or not previous_action_payload.get("previous_unittests_issue"):
            raise InternalClientError(
                "Internal client error: Previous action payload does not contain previous unit tests issue."
            )
        unittests_issue = previous_action_payload["previous_unittests_issue"]
        context = render_context.unit_tests_running_context
        api = render_context.codeplain_api
        frid, module_name = render_context.frid_context.frid, render_context.module_name
        changed_files_before = set(context.changed_files)
        log_path = render_context.script_execution_history.latest_unit_test_output_path
        if log_path:
            agent_tools.register_log_path(log_path, render_context)

        if context.agent_session_id is None:
            console.info("Starting an agent session to fix the unit tests.")
            # Cached read results point at earlier turns, which a new session does not have.
            context.tool_result_cache.clear()
            response = api.agent_start(
                TASK_TYPE,
                self._build_task_params(render_context, unittests_issue),
                frid,
                module_name,
                render_context.run_state,
            )
            context.agent_session_id = response["session_id"]
        else:
            console.info(f"Continuing agent session {context.agent_session_id} with the new unit tests failure.")
            tool_results = context.pending_tool_results + [
                {
                    "call_id": context.pending_submit_call_id,
                    "output": "The fix was applied, but the unit tests still fail."
                    + agent_tools.full_log_pointer(log_path),
                    "test_output": unittests_issue,
                }
            ]
            context.pending_tool_results, context.pending_submit_call_id = [], None
            response = api.agent_continue(
                context.agent_session_id, tool_results, frid, module_name, render_context.run_state
            )

        submitted = False
        turns = 0
        while response.get("status") == "tool_calls" and turns < MAX_AGENT_TURNS_PER_ATTEMPT:
            turns += 1
            calls = response["calls"]
            submit_call = next((call for call in calls if call["name"] == SUBMIT_FIX_TOOL), None)
            tool_results = agent_tools.execute_calls(
                [call for call in calls if call is not submit_call], render_context
            )
            if submit_call is not None:
                context.pending_submit_call_id = submit_call["id"]
                context.pending_tool_results = tool_results
                submitted = True
                console.info(f"Agent submitted a fix: {submit_call['args'].get('changes_made', '')}")
                break
            response = api.agent_continue(
                context.agent_session_id, tool_results, frid, module_name, render_context.run_state
            )

        if not submitted:
            # The session ended without a submission (finished in text, failed, or used up this
            # attempt's turn budget) — a fresh session is started if the tests still fail.
            status = response.get("status")
            if status == "failed":
                console.warning(f"Agent session failed: {response.get('error', 'unknown error')}")
            elif status == "tool_calls":
                console.warning(f"Agent used {MAX_AGENT_TURNS_PER_ATTEMPT} turns without submitting a fix.")
            context.previous_session_id = context.agent_session_id
            context.agent_session_id, context.pending_submit_call_id, context.pending_tool_results = None, None, []

        console.print_files(
            "Files changed while fixing unit tests:",
            render_context.build_folder,
            {path: "" for path in sorted(context.changed_files - changed_files_before)},
            style=console.OUTPUT_STYLE,
        )
        return self.SUCCESSFUL_OUTCOME, None

    @staticmethod
    def _build_task_params(render_context: RenderContext, unittests_issue: str) -> dict:
        frid = render_context.frid_context.frid
        specifications, _ = plain_spec.get_specifications_for_frid(render_context.plain_source_tree, frid)
        context = render_context.unit_tests_running_context
        task_params = {
            "definitions": "\n".join(specifications.get(plain_spec.DEFINITIONS, [])),
            "non_functional_requirements": "\n".join(specifications.get(plain_spec.NON_FUNCTIONAL_REQUIREMENTS, [])),
            "functional_requirements": FixUnitTests._functional_requirements_section(render_context, specifications),
            "linked_resources": render_context.frid_context.linked_resources,
            "build_folder": render_context.build_folder,
            "module_name": render_context.module_name,
            "unittests_script_content": FixUnitTests._read_script(render_context.unittests_script),
            "unittests_issue": unittests_issue,
            "unittests_log_path": render_context.script_execution_history.latest_unit_test_output_path,
            "file_tree": FixUnitTests._file_tree(render_context.build_folder),
            "relevant_files": FixUnitTests._relevant_files(
                render_context.build_folder, render_context.frid_context.changed_files | context.changed_files
            ),
        }
        if context.previous_session_id:
            task_params["previous_session_id"] = context.previous_session_id
        return task_params

    @staticmethod
    def _file_tree(build_folder: str) -> str:
        paths = []
        for root, dirs, files in os.walk(build_folder):
            dirs[:] = sorted(d for d in dirs if d not in agent_tools.GREP_EXCLUDED_DIRS and not d.startswith("."))
            paths.extend(os.path.relpath(os.path.join(root, name), build_folder) for name in sorted(files))
            if len(paths) > MAX_FILE_TREE_ENTRIES:
                return "\n".join(paths[:MAX_FILE_TREE_ENTRIES]) + "\n... [more files not listed]"
        return "\n".join(paths)

    @staticmethod
    def _relevant_files(build_folder: str, file_names: set[str]) -> dict[str, str]:
        """Contents of the given build-relative files, smallest first, within MAX_RELEVANT_FILES_CHARS."""
        contents = {}
        for name in file_names:
            full_path = os.path.join(build_folder, name)
            if os.path.isfile(full_path):
                with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                    contents[name] = f.read()
        relevant, total = {}, 0
        for name in sorted(contents, key=lambda n: (len(contents[n]), n)):
            if total + len(contents[name]) > MAX_RELEVANT_FILES_CHARS:
                break
            relevant[name] = contents[name]
            total += len(contents[name])
        return dict(sorted(relevant.items()))

    @staticmethod
    def _functional_requirements_section(render_context: RenderContext, specifications: dict) -> str:
        sections = []
        for module_name, functionalities in render_context.get_required_modules_functionalities().items():
            sections.append(
                f"### Module: {module_name} (Already Implemented, for context)\n" + "\n".join(functionalities)
            )
        current = specifications.get(plain_spec.FUNCTIONAL_REQUIREMENTS, [])
        if len(current) > 1:
            sections.append(
                f"### Module: {render_context.module_name} (Already Implemented, for context)\n"
                + "\n".join(current[:-1])
            )
        if current:
            sections.append(f"### Module: {render_context.module_name} (Currently Being Implemented)\n{current[-1]}")
        return "\n\n".join(sections)

    @staticmethod
    def _read_script(script: str | None) -> str:
        if not script:
            return ""
        try:
            with open(file_utils.add_current_path_if_no_path(script), "r", encoding="utf-8") as f:
                return f.read()
        except OSError:
            return ""
