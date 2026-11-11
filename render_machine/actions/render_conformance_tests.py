import os
from typing import Any, Optional

import file_utils
import plain_spec
from memory_management import MemoryManager
from plain2code_console import console
from plain2code_utils import qualified_frid
from render_machine import agent_session, agent_tools, traceability
from render_machine.actions import prepare_testing_environment
from render_machine.actions.base_action import BaseAction
from render_machine.actions.fix_conformance_test import FixConformanceTest, _folder_files, _stringify_scenarios
from render_machine.actions.fix_unit_tests import FixUnitTests
from render_machine.implementation_code_helpers import ImplementationCodeHelpers
from render_machine.render_context import RenderContext
from render_machine.render_types import AcceptanceTestPhase, TestExecutionPhase

TASK_TYPE = "write_conformance_tests"
SUBMIT_TOOL = "submit_conformance_tests"
WRITER_TOOLS = frozenset(
    {"read_file", "grep", "ls_files", "edit_file", "write_file", "delete_file", "run_conformance_tests"}
)
# Upper bound on the turns the client executes; the server forces the submission earlier (35) and ends at 40.
MAX_AGENT_TURNS = 50
# Submissions whose tests do not trace to the scenarios are sent back this many times before they are accepted
# with a warning.
MAX_TRACEABILITY_CORRECTIONS = 2
SUGGESTED_MAX_CONFORMANCE_TESTS_CODE_LINES = 200
TRACEABILITY_PROBLEMS_MESSAGE = (
    "The tests you listed do not match the scenarios:\n{problems}\nCorrect the tests (or the list, if it is the list "
    "that is wrong) and call submit_conformance_tests again."
)
ACCEPTED_MESSAGE = "The conformance tests were accepted."


class RenderConformanceTests(BaseAction):
    """Write the conformance tests of a functionality (or the test of one acceptance scenario) with a server-side
    agent session (task write_conformance_tests).

    The agent writes only in the functionality's conformance tests folder, may run the tests itself, and ends with
    submit_conformance_tests listing every test and its scenario. A list that does not trace to the scenarios goes
    back to it to correct. When its last run passed and nothing changed since, the harness's run is skipped.
    """

    SUCCESSFUL_OUTCOME = "conformance_test_rendered"

    def execute(self, render_context: RenderContext, _previous_action_payload: Any | None):
        prepare_testing_environment.start_in_background(render_context)
        if self._should_render_conformance_tests(render_context):
            return self._render_conformance_tests(render_context)
        else:
            return self._render_acceptance_test(render_context)

    def _should_render_conformance_tests(self, render_context: RenderContext) -> bool:
        """Check if we should render full conformance tests (first time) vs just acceptance tests (incremental)."""

        ctx = render_context.conformance_tests_running_context

        # If we're in regression phase, tests already exist - don't render anything new
        if ctx.execution_phase == TestExecutionPhase.RUNNING_REGRESSION:
            return True  # Return True to skip rendering (conformance tests already exist)

        # Render full conformance tests when:
        # 1. NOT_STARTED: First render, before acceptance tests begin
        if ctx.acceptance_test_phase == AcceptanceTestPhase.NOT_STARTED:
            return True

        # 2. NOT_APPLICABLE: No acceptance tests defined, always use full conformance test path
        if ctx.acceptance_test_phase == AcceptanceTestPhase.NOT_APPLICABLE:
            # Return True so _render_conformance_tests() is called, which will skip if tests already exist
            return True

        # 3. All other cases (IN_PROGRESS, COMPLETED): Render acceptance tests incrementally
        return False

    def _render_conformance_tests(self, render_context: RenderContext):
        ctx = render_context.conformance_tests_running_context
        if not isinstance(ctx.current_testing_frid_specifications, dict):
            # A required module's functionality, known only by its text: its tests exist and are not written here.
            return self.SUCCESSFUL_OUTCOME, None
        scenarios = ctx.current_testing_frid_specifications.get(plain_spec.CONFORMANCE_TESTS) or []
        if not ctx.current_conformance_tests_exist():
            console.info("Implementing test requirements:")
            console.print_list(
                ctx.current_testing_frid_specifications[plain_spec.TEST_REQUIREMENTS],
                style=console.INFO_STYLE,
            )
            fr_subfolder_name = render_context.codeplain_api.generate_folder_name_from_functional_requirement(
                frid=ctx.current_testing_frid,
                module_name=ctx.current_testing_module_name,
                functional_requirement=ctx.current_testing_frid_specifications[plain_spec.FUNCTIONAL_REQUIREMENTS][-1],
                existing_folder_names=render_context.conformance_tests.fetch_existing_conformance_test_folder_names(
                    ctx.current_testing_module_name
                ),
                run_state=render_context.run_state,
            )
            conformance_tests_folder_name = os.path.join(
                render_context.conformance_tests.get_module_conformance_tests_folder(render_context.module_name),
                fr_subfolder_name,
            )
            console.debug(f"Storing conformance test files in subfolder {conformance_tests_folder_name}/")
            ctx.get_conformance_tests_json(ctx.current_testing_module_name)[ctx.current_testing_frid] = {
                "folder_name": conformance_tests_folder_name,
                "functional_requirement": render_context.frid_context.specifications[
                    plain_spec.FUNCTIONAL_REQUIREMENTS
                ][-1],
            }
        else:
            conformance_tests_folder_name = ctx.get_current_conformance_test_folder_name()

        if not scenarios:
            # The functionality states no conformance scenario, so there is no test to write.
            console.info(
                f"Functionality {render_context.testing_functionality_label()} has no conformance scenario; "
                "no conformance tests are written for it."
            )
            return self.SUCCESSFUL_OUTCOME, None

        tests_folder = os.path.normpath(os.path.abspath(conformance_tests_folder_name))
        params = self._common_params(render_context, tests_folder)
        params["conformance_scenarios"] = _stringify_scenarios(scenarios)
        params["suggested_max_lines"] = SUGGESTED_MAX_CONFORMANCE_TESTS_CODE_LINES
        params["example_tests_files"] = self._example_tests_files(render_context, tests_folder)
        params["conformance_tests_files"] = _folder_files(tests_folder)

        console.info("Writing the conformance tests with an agent.")
        self._write(render_context, tests_folder, params, [scenario["id"] for scenario in scenarios])
        console.print_files(
            "Conformance test files generated:",
            tests_folder,
            {name: "" for name in file_utils.list_all_text_files(tests_folder)} if os.path.isdir(tests_folder) else {},
            style=console.OUTPUT_STYLE,
        )
        return self.SUCCESSFUL_OUTCOME, None

    def _render_acceptance_test(self, render_context: RenderContext):
        if plain_spec.ACCEPTANCE_TESTS not in render_context.frid_context.specifications:
            # If there are no acceptance tests defined, continue.
            return self.SUCCESSFUL_OUTCOME, None

        ctx = render_context.conformance_tests_running_context
        specifications = render_context.frid_context.specifications
        acceptance_scenarios = specifications[plain_spec.ACCEPTANCE_TESTS]

        # Get the current acceptance scenario being rendered (completed count is 1-based)
        scenario = acceptance_scenarios[ctx.acceptance_tests_completed - 1]

        # This action is entered through more than one path, and the counter above belongs to the
        # state machine, not to the action. A path that enters it again without the counter moving
        # asks for a scenario that already has a test. Rendering it again costs a call and lets the
        # render write a second test for the one scenario.
        if scenario["id"] in ctx.rendered_acceptance_scenarios:
            console.warning(f"The acceptance scenario {scenario['id']} already has a test. It is not rendered again.")
            return self.SUCCESSFUL_OUTCOME, None

        console.info(f"Generating acceptance test for scenario {scenario['id']}.")
        tests_folder = os.path.normpath(os.path.abspath(ctx.get_current_conformance_test_folder_name()))
        conformance_scenarios = specifications.get(plain_spec.CONFORMANCE_TESTS) or []
        params = self._common_params(render_context, tests_folder)
        params["conformance_scenarios"] = _stringify_scenarios(conformance_scenarios)
        params["acceptance_scenario"] = _stringify_scenarios([scenario])
        params["conformance_tests_files"] = _folder_files(tests_folder)

        # Every conformance scenario and every acceptance scenario up to this one must have a test afterwards. The
        # earlier acceptance scenarios already have one; the writer is shown them so it can list their tests.
        acceptance_ids = [s["id"] for s in acceptance_scenarios]
        earlier = acceptance_scenarios[: acceptance_ids.index(scenario["id"])]
        if earlier:
            params["earlier_acceptance_scenarios"] = _stringify_scenarios(earlier)
        expected = [s["id"] for s in conformance_scenarios] + [s["id"] for s in earlier] + [scenario["id"]]
        self._write(render_context, tests_folder, params, expected)
        ctx.rendered_acceptance_scenarios.append(scenario["id"])

        console.print_files(
            f"Conformance test files in folder {tests_folder} updated:",
            tests_folder,
            {name: "" for name in file_utils.list_all_text_files(tests_folder)} if os.path.isdir(tests_folder) else {},
            style=console.OUTPUT_STYLE,
        )
        return self.SUCCESSFUL_OUTCOME, None

    # ---------------------------------------------------------------------------------------------------------
    # The agent
    # ---------------------------------------------------------------------------------------------------------

    def _write(self, render_context: RenderContext, tests_folder: str, params: dict, scenario_ids: list[str]) -> None:
        """Run the writer until it submits tests that trace to `scenario_ids` (or the corrections run out)."""
        ctx = render_context.conformance_tests_running_context
        workspace = self._workspace(render_context, tests_folder)
        api = render_context.codeplain_api
        frid, module_name = render_context.frid_context.frid, render_context.module_name
        response = api.agent_start(TASK_TYPE, params, frid, module_name, render_context.run_state)
        session_id = response["session_id"]
        corrections = 0
        while True:
            run = agent_session.run_until_submit(
                render_context, session_id, response, SUBMIT_TOOL, workspace, MAX_AGENT_TURNS
            )
            if run.submit_call is None:
                console.warning(f"The conformance tests writer ended without submitting ({run.status}).")
                return
            args = run.submit_call.get("args") or {}
            files = file_utils.get_existing_files_content(tests_folder, file_utils.list_all_text_files(tests_folder))
            traced = traceability.check_traceability(scenario_ids, args.get("tests") or [], files, tests_folder)
            problems = traceability.describe_problems(traced)
            if not problems or run.session_ended or corrections >= MAX_TRACEABILITY_CORRECTIONS:
                break
            corrections += 1
            console.info("The conformance tests do not match the scenarios; sending them back to the writer.")
            result = agent_session.submit_result(
                run.submit_call["id"], TRACEABILITY_PROBLEMS_MESSAGE.format(problems=problems)
            )
            response = api.agent_continue(
                session_id, run.tool_results + [result], frid, module_name, render_context.run_state
            )

        console.info(f"Agent submitted the conformance tests: {args.get('summary', '')}")
        self._warn_about_untraced_scenarios(render_context, traced)
        last_run = workspace.last_test_run
        if last_run is not None and last_run[1].get("output", "").startswith("The conformance tests passed"):
            # Keyed on the files as the run left them; RunConformanceTests skips its run while they are the same.
            ctx.verified_passing_run = (last_run[0], None)

    @staticmethod
    def _workspace(render_context: RenderContext, tests_folder: str) -> agent_tools.AgentWorkspace:
        ctx = render_context.conformance_tests_running_context
        build_folder = os.path.normpath(os.path.abspath(render_context.build_folder))
        prepared_signature = None
        if render_context.prepare_environment_script and not ctx.should_prepare_testing_environment:
            # The environment is already prepared for the build folder as it is.
            prepared_signature = prepare_testing_environment._build_folder_signature(render_context.build_folder)
        return agent_tools.AgentWorkspace(
            build_folder=build_folder,
            writable_folders=[tests_folder],
            readable_folders=[
                agent_tools.rendered_modules_folder(render_context),
                *FixConformanceTest._readable_conformance_tests_folders(render_context),
            ],
            allowed_tools=WRITER_TOOLS,
            render_context=render_context,
            conformance_tests_folder=tests_folder,
            prepared_signature=prepared_signature,
        )

    @staticmethod
    def _common_params(render_context: RenderContext, tests_folder: str) -> dict:
        ctx = render_context.conformance_tests_running_context
        frid = render_context.frid_context.frid
        specifications, _ = plain_spec.get_specifications_for_frid(render_context.plain_source_tree, frid)
        tested = ctx.current_testing_frid_specifications
        tested = tested if isinstance(tested, dict) else {}
        _, memory_files = MemoryManager.fetch_memory_files(render_context.memory_manager.memory_folder)
        return {
            "definitions": "\n".join(specifications.get(plain_spec.DEFINITIONS, [])),
            "test_requirements": "\n".join(
                tested.get(plain_spec.TEST_REQUIREMENTS) or specifications.get(plain_spec.TEST_REQUIREMENTS, [])
            ),
            "functional_requirements": FixUnitTests._functional_requirements_section(render_context, specifications),
            "linked_resources": render_context.frid_context.linked_resources,
            "module_name": render_context.module_name,
            "build_folder": os.path.normpath(os.path.abspath(render_context.build_folder)),
            "conformance_tests_folder": tests_folder,
            **agent_tools.modules_layout_params(render_context),
            "readable_conformance_tests_folders": FixConformanceTest._readable_conformance_tests_folders(
                render_context
            ),
            "conformance_tests_script_content": FixUnitTests._read_script(render_context.conformance_tests_script),
            "prepare_environment_script_content": FixUnitTests._read_script(render_context.prepare_environment_script),
            "tested_functionality_id": qualified_frid(ctx.current_testing_module_name, ctx.current_testing_frid),
            "tested_functionality": (tested.get(plain_spec.FUNCTIONAL_REQUIREMENTS) or [""])[-1],
            "previous_conformance_tests": _previous_conformance_tests(render_context),
            "memory_files": memory_files,
            "code_diff": _functionality_code_diff(render_context),
            "file_tree": FixUnitTests._file_tree(render_context.build_folder),
            "relevant_files": FixUnitTests._relevant_files(
                render_context.build_folder, set(render_context.frid_context.changed_files)
            ),
        }

    @staticmethod
    def _example_tests_files(render_context: RenderContext, tests_folder: str) -> dict[str, str]:
        """The files of the most recently written conformance tests of another functionality of this module: they
        passed, so they show a test setup that works here."""
        ctx = render_context.conformance_tests_running_context
        entries = list(ctx.get_conformance_tests_json(ctx.current_testing_module_name).items())
        for entry_frid, entry in reversed(entries):
            folder = entry.get("folder_name")
            if entry_frid == ctx.current_testing_frid or not folder:
                continue
            folder = os.path.normpath(os.path.abspath(folder))
            if folder != tests_folder and os.path.isdir(folder):
                files = _folder_files(folder)
                if files:
                    return files
        return {}

    @staticmethod
    def _warn_about_untraced_scenarios(render_context: RenderContext, traced: dict) -> None:
        """Warn about a scenario with no test, a test that implements no scenario, and a scenario with more than
        one test, as the writer left them."""
        frid = render_context.testing_functionality_label()
        if traced.get("missing"):
            console.warning(f"No conformance test was generated for {frid} scenarios: {', '.join(traced['missing'])}.")
        if traced.get("extra"):
            console.warning(f"Conformance tests of {frid} that implement no scenario: {', '.join(traced['extra'])}.")
        if traced.get("duplicated"):
            console.warning(
                f"More than one conformance test of {frid} implements the scenarios: {', '.join(traced['duplicated'])}."
            )


def _previous_conformance_tests(render_context: RenderContext) -> str:
    """The earlier functionalities of the module that have conformance tests, and where those tests are."""
    ctx = render_context.conformance_tests_running_context
    lines = []
    for entry_frid, entry in ctx.get_conformance_tests_json(ctx.current_testing_module_name).items():
        if entry_frid == ctx.current_testing_frid or not entry.get("folder_name"):
            continue
        lines.append(f"- {entry.get('functional_requirement', entry_frid)}\n  Tests: {entry['folder_name']}")
    return "\n".join(lines)


def _functionality_code_diff(render_context: RenderContext) -> Optional[dict]:
    """The implementation changes of the functionality being tested; empty when they cannot be determined."""
    try:
        return ImplementationCodeHelpers.get_code_diff(
            render_context.build_folder, render_context.plain_source_tree, render_context.frid_context.frid
        )
    except Exception as e:  # The diff only helps; a missing commit (e.g. the first functionality) must not stop it.
        console.debug(f"Could not compute the code diff of the functionality: {e}")
        return {}
