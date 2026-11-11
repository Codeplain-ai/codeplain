import os
from typing import Any

import file_utils
import plain_spec
import render_machine.render_utils as render_utils
from memory_management import MemoryManager
from plain2code_console import RETRY_COLOR, console
from plain2code_exceptions import FunctionalRequirementTooComplex
from render_machine import agent_session, agent_tools
from render_machine.actions.base_action import BaseAction
from render_machine.actions.fix_unit_tests import FixUnitTests
from render_machine.implementation_code_helpers import ImplementationCodeHelpers
from render_machine.render_context import MAX_UNITTEST_FIX_ATTEMPTS, RenderContext
from render_machine.render_types import RenderError

MAX_CODE_GENERATION_RETRIES = 2

TASK_TYPE = "implement_functionality"
TASK_TYPE_WITHOUT_UNIT_TESTS = "implement_functionality_without_unit_tests"
SUBMIT_TOOL = "submit_implementation"
IMPLEMENTER_TOOLS = frozenset({"read_file", "grep", "ls_files", "edit_file", "write_file", "delete_file"})
# Upper bound on the turns the client executes; the server forces the submission earlier (45) and ends at 50.
MAX_AGENT_TURNS = 60
# The build folder is seeded in full when it fits; otherwise its most recently changed files that fit. Seeded
# files are what saves the agent its reading turns.
MAX_SEEDED_FILES_CHARS = 80_000


class ImplementingAgentFailed(Exception):
    pass


class RenderFunctionalRequirement(BaseAction):
    """Implement the current functionality with a server-side agent session (task implement_functionality).

    The agent is seeded with the specification and the build folder, writes the code (and unit tests) with file
    tools executed here, runs the unit tests itself, and ends with submit_implementation. When its last unit-test
    run passed and no file changed since, the unit-test loop that follows does not run them again.
    """

    SUCCESSFUL_OUTCOME = "code_and_unit_tests_generated"
    FUNCTIONAL_REQUIREMENT_TOO_COMPLEX_OUTCOME = "functional_requirement_too_complex"
    ITERATION_LIMIT_EXCEEDED_OUTCOME = "frid_iteration_limit_exceeded"

    @staticmethod
    def functionality_heading(render_context) -> str:
        """The block logged when a functionality starts: its module, its ID, and where it stands."""
        msg = "-------------------------------------\n"
        msg += f"Module: {render_context.module_name}\n"
        msg += f"Rendering functionality {render_context.frid_context.frid}"
        position = render_context.current_functionality_position()
        if position:
            msg += f" ({position})"
        if render_context.frid_context.functional_requirement_render_attempts > 1:
            msg += f", attempt number {render_context.frid_context.functional_requirement_render_attempts}/{MAX_CODE_GENERATION_RETRIES}"
        msg += f":\n{render_context.frid_context.functional_requirement_text}\n"
        msg += "-------------------------------------"
        return msg

    def execute(self, render_context: RenderContext, _previous_action_payload: Any | None):
        frid_context = render_context.frid_context
        if frid_context.functional_requirement_render_attempts >= MAX_CODE_GENERATION_RETRIES:
            # A render attempt is only restarted once its unit tests used up every fix round.
            functionality = render_context.functionality_label()
            error_msg = (
                f"Functionality {functionality}: unit tests still fail after "
                f"{MAX_CODE_GENERATION_RETRIES} render attempts, {MAX_UNITTEST_FIX_ATTEMPTS} fix rounds each."
            )
            last_output_path = render_context.script_execution_history.latest_unit_test_output_path
            if last_output_path:
                error_msg += f" Last unit tests output: {last_output_path}"
            render_context.last_error_message = error_msg
            return self.ITERATION_LIMIT_EXCEEDED_OUTCOME, RenderError.encode(message=error_msg).to_payload()

        frid_context.functional_requirement_render_attempts += 1
        frid_context.implementation_unit_tests_passed = False
        frid_context.implementation_unit_tests_log_path = None

        if frid_context.functional_requirement_render_attempts > 1:
            console.info(
                f"↻ Unittests could not be fixed after rendering the functionality. "
                f"Restarting rendering functionality "
                f"{render_context.functionality_label()} from scratch.",
                color=RETRY_COLOR,
            )

        render_utils.revert_changes_for_frid(render_context)
        # The revert undid every change of an earlier attempt.
        frid_context.changed_files.clear()
        existing_files, existing_files_content = ImplementationCodeHelpers.fetch_existing_files(
            render_context.build_folder
        )

        console.info(self.functionality_heading(render_context))

        try:
            submission = self._implement(render_context, existing_files, existing_files_content)
            if submission is not None and submission.get("functionality_too_complex"):
                console.info("The implementing agent judged the functionality too complex to implement in one go.")
                render_context.codeplain_api.check_functional_requirement_breakdown(
                    frid_context.frid,
                    render_context.plain_source_tree,
                    frid_context.linked_resources,
                    existing_files_content,
                    render_context.module_name,
                    render_context.get_required_modules_functionalities(),
                    render_context.run_state,
                )
                console.info("No breakdown of the functionality is feasible; the implementation is accepted.")
        except FunctionalRequirementTooComplex as e:
            error_message = f"The functionality:\n{frid_context.functional_requirement_text}\n is too complex to be implemented. Please break down the functionality into smaller parts."
            if e.proposed_breakdown:
                error_message += "\nProposed breakdown:"
                for _, part in e.proposed_breakdown["functional_requirements"].items():
                    error_message += f"\n  - {part}"

            return (
                self.FUNCTIONAL_REQUIREMENT_TOO_COMPLEX_OUTCOME,
                RenderError.encode(
                    message=error_message,
                    error_type="COMPLEXITY_ERROR",
                    frid=frid_context.frid,
                    proposed_breakdown=e.proposed_breakdown,
                ).to_payload(),
            )

        console.print_files(
            "Files generated or updated:",
            render_context.build_folder,
            {path: "" for path in sorted(frid_context.changed_files)},
            style=console.OUTPUT_STYLE,
        )
        return self.SUCCESSFUL_OUTCOME, None

    def _implement(self, render_context: RenderContext, existing_files: list, existing_files_content: dict):
        """Run the implementing agent; returns its submit_implementation arguments (None when the server answered
        from the render cache)."""
        frid_context = render_context.frid_context
        with_unit_tests = render_context.should_run_unit_tests()
        render_utils.print_inputs(render_context, existing_files_content, "Files sent as input to code generation:")
        response = render_context.codeplain_api.agent_start(
            TASK_TYPE if with_unit_tests else TASK_TYPE_WITHOUT_UNIT_TESTS,
            self._build_task_params(render_context, existing_files_content, with_unit_tests),
            frid_context.frid,
            render_context.module_name,
            render_context.run_state,
            plain_source_tree=render_context.plain_source_tree,
        )
        if response.get("cached_files"):
            console.info("Using the render cache for this functionality.")
            _, changed_files = file_utils.update_build_folder_with_rendered_files(
                render_context.build_folder, existing_files, response["cached_files"]
            )
            frid_context.changed_files.update(changed_files)
            return None

        workspace = self._workspace(render_context, with_unit_tests)
        console.info("Implementing the functionality with an agent.")
        run = agent_session.run_until_submit(
            render_context, response["session_id"], response, SUBMIT_TOOL, workspace, MAX_AGENT_TURNS
        )
        if run.submit_call is None:
            if not frid_context.changed_files:
                raise ImplementingAgentFailed(
                    f"The implementing agent ended without implementing functionality "
                    f"{render_context.functionality_label()} ({run.status}: {run.response.get('error', '')})."
                )
            # What was written is kept; the unit and conformance tests decide whether it is enough.
            console.warning(f"The implementing agent ended without submitting ({run.status}); continuing with tests.")
            return {}

        args = run.submit_call.get("args") or {}
        console.info(f"Agent submitted the implementation: {args.get('summary', '')}")
        if with_unit_tests and workspace.unit_tests_passed:
            frid_context.implementation_unit_tests_passed = True
            frid_context.implementation_unit_tests_log_path = workspace.unit_tests_passed_log_path
        return args

    @staticmethod
    def _workspace(render_context: RenderContext, with_unit_tests: bool) -> agent_tools.AgentWorkspace:
        build_folder = os.path.normpath(os.path.abspath(render_context.build_folder))

        def track(full_path: str) -> None:
            render_context.frid_context.changed_files.add(os.path.relpath(full_path, build_folder))

        return agent_tools.AgentWorkspace(
            build_folder=build_folder,
            writable_folders=[build_folder],
            readable_folders=[agent_tools.rendered_modules_folder(render_context)],
            after_change=track,
            allowed_tools=IMPLEMENTER_TOOLS | ({"run_unit_tests"} if with_unit_tests else set()),
            render_context=render_context,
        )

    @staticmethod
    def _build_task_params(render_context: RenderContext, existing_files_content: dict, with_unit_tests: bool) -> dict:
        frid_context = render_context.frid_context
        specifications, _ = plain_spec.get_specifications_for_frid(render_context.plain_source_tree, frid_context.frid)
        seeded, complete = seeded_files(render_context.build_folder, existing_files_content)
        _, memory_files = MemoryManager.fetch_memory_files(render_context.memory_manager.memory_folder)
        params = {
            "definitions": "\n".join(specifications.get(plain_spec.DEFINITIONS, [])),
            "non_functional_requirements": "\n".join(specifications.get(plain_spec.NON_FUNCTIONAL_REQUIREMENTS, [])),
            "functional_requirements": FixUnitTests._functional_requirements_section(render_context, specifications),
            "functionality": (specifications.get(plain_spec.FUNCTIONAL_REQUIREMENTS) or [""])[-1],
            "linked_resources": frid_context.linked_resources,
            "build_folder": os.path.normpath(os.path.abspath(render_context.build_folder)),
            "module_name": render_context.module_name,
            **agent_tools.modules_layout_params(render_context),
            "is_new_project": not existing_files_content,
            "file_tree": FixUnitTests._file_tree(render_context.build_folder) if existing_files_content else "",
            "existing_files": seeded,
            "existing_files_complete": complete,
            "memory_files": memory_files,
        }
        if with_unit_tests:
            params["unittests_script_content"] = FixUnitTests._read_script(render_context.unittests_script)
        return params


def seeded_files(build_folder: str, contents: dict[str, str]) -> tuple[dict[str, str], bool]:
    """The build folder files to seed an agent with: all of them when they fit MAX_SEEDED_FILES_CHARS (and True),
    otherwise the most recently changed ones that fit (and False)."""
    if sum(len(content) for content in contents.values()) <= MAX_SEEDED_FILES_CHARS:
        return dict(sorted(contents.items())), True

    def changed_at(name: str) -> float:
        try:
            return os.path.getmtime(os.path.join(build_folder, name))
        except OSError:
            return 0.0

    selected: dict[str, str] = {}
    total = 0
    for name in sorted(contents, key=lambda n: (-changed_at(n), n)):
        if total + len(contents[name]) > MAX_SEEDED_FILES_CHARS:
            continue
        selected[name] = contents[name]
        total += len(contents[name])
    return dict(sorted(selected.items())), False
