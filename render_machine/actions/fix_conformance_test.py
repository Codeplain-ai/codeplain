import os
from typing import Any, Optional

import file_utils
import plain_spec
from memory_management import MemoryManager
from plain2code_console import RETRY_COLOR, console
from plain2code_exceptions import InternalClientError
from plain2code_utils import qualified_frid
from render_machine.actions.base_action import BaseAction
from render_machine.actions.fix_unit_tests import FixUnitTests
from render_machine.conformance_fix_conversation import (
    CONFLICTING_REQUIREMENTS,
    ApprovedFix,
    ConformanceFixConversation,
)
from render_machine.implementation_code_helpers import ImplementationCodeHelpers
from render_machine.render_context import RenderContext
from render_machine.render_types import ConformanceFixAgents, RenderError, TestExecutionPhase

MAX_CONFORMANCE_TEST_FIX_ATTEMPTS = 20
MAX_CONFORMANCE_TEST_RERENDER_ATTEMPTS = 1
# Bound on the conformance tests files a new session is seeded with.
MAX_CONFORMANCE_TESTS_FILES_CHARS = 60_000


class FixConformanceTest(BaseAction):
    """Fix failing conformance tests through a conversation between a fixer agent and a reviewer agent (see
    ConformanceFixConversation). The action returns once the reviewer approved a fix; the state machine then
    runs the tests again (after the unit tests, when the implementation code changed). The two agents live as
    long as the conformance tests of the same functionality keep failing, so each next execution continues
    them with the new failure output."""

    IMPLEMENTATION_CODE_NOT_UPDATED = "implementation_code_not_updated"
    IMPLEMENTATION_CODE_UPDATED = "implementation_code_updated"
    LIMIT_EXCEEDED_OUTCOME = "conformance_test_fix_limit_exceeded"
    REGENERATE_CONFORMANCE_TESTS_OUTCOME = "regenerate_conformance_tests"

    def execute(self, render_context: RenderContext, previous_action_payload: Any | None):
        ctx = render_context.conformance_tests_running_context
        ctx.fix_attempts += 1

        if ctx.fix_attempts >= MAX_CONFORMANCE_TEST_FIX_ATTEMPTS:
            ctx.conformance_fix_agents = None
            if ctx.conformance_tests_render_attempts >= MAX_CONFORMANCE_TEST_RERENDER_ATTEMPTS:
                error_msg = f"The renderer was unable to produce an implementation that passes conformance tests for functionality '{render_context.frid_context.frid}' after many attempts. Please review and rewrite the specification. (Render ID: {render_context.run_state.render_id})"
                render_context.last_error_message = error_msg
                return (
                    self.LIMIT_EXCEEDED_OUTCOME,
                    RenderError.encode(message=error_msg).to_payload(),
                )
            else:
                ctx.regenerating_conformance_tests = True
                return self.REGENERATE_CONFORMANCE_TESTS_OUTCOME, None

        console.info(f"Running conformance tests attempt {ctx.fix_attempts + 1}.")
        console.info(f"Fixing conformance test for functionality {render_context.testing_functionality_label()}.")

        if not previous_action_payload or not previous_action_payload.get("previous_conformance_tests_issue"):
            raise InternalClientError(
                "Internal client error: Previous action payload does not contain previous conformance tests issue."
            )
        issue = previous_action_payload["previous_conformance_tests_issue"]

        journal = render_context.memory_manager.journal
        journal.record_result(ctx.current_testing_module_name, ctx.current_testing_frid, passed=False)
        journal.open_attempt(ctx.current_testing_module_name, ctx.current_testing_frid, ctx.fix_attempts, issue)

        # Reset the conflicting requirement count if the current testing functionality is not the same as the
        # previously conflicting functionality.
        if (ctx.conflicting_module_name, ctx.conflicting_frid) != (
            ctx.current_testing_module_name,
            ctx.current_testing_frid,
        ):
            ctx.conflicting_requirement_count = 0

        agents = self._agents_for_current_tests(render_context)
        common_params = self._common_params(render_context, agents)
        conversation = ConformanceFixConversation(
            render_context, agents, self._fixer_params(render_context, common_params), common_params
        )
        log_path = render_context.script_execution_history.latest_conformance_test_output_path
        fix = conversation.fix(issue, log_path)
        if fix is None:
            journal.record_fix(
                ctx.current_testing_module_name, ctx.current_testing_frid, None, [], "IMPLEMENTATION_CODE", None, None
            )
            return self.IMPLEMENTATION_CODE_NOT_UPDATED, None

        return self._apply_outcome(render_context, conversation, fix)

    # ---------------------------------------------------------------------------------------------------------

    def _apply_outcome(self, render_context: RenderContext, conversation: ConformanceFixConversation, fix: ApprovedFix):
        """Record the approved fix and pick the next step: the unit tests when the implementation code changed,
        otherwise the conformance tests right away. A conflict confirmed a second time ends the render."""
        ctx = render_context.conformance_tests_running_context
        build_folder = conversation.build_folder
        implementation_files = {path for path in fix.changed_files if path.startswith(build_folder + os.sep)}
        code_diff = conversation.diff(fix.changed_files)
        implementation_code_diff = {
            name: diff for name, diff in code_diff.items() if os.path.join(build_folder, name) in implementation_files
        }
        summary = {"hypothesis": fix.args.get("root_cause"), "approach": fix.args.get("changes_made")}

        if fix.args.get("failing_reason") == CONFLICTING_REQUIREMENTS:
            self._handle_conflict(render_context, fix)

        console.print_files(
            "Files changed while fixing conformance tests:",
            build_folder,
            {conversation.display_name(path): "" for path in sorted(fix.changed_files)},
            style=console.OUTPUT_STYLE,
        )
        target = "IMPLEMENTATION_CODE" if implementation_files else "CONFORMANCE_TESTS"
        render_context.memory_manager.journal.record_fix(
            ctx.current_testing_module_name,
            ctx.current_testing_frid,
            summary,
            sorted(conversation.display_name(path) for path in fix.changed_files),
            target,
            code_diff,
            None,
        )

        if not implementation_files:
            return self.IMPLEMENTATION_CODE_NOT_UPDATED, None

        ctx.should_prepare_testing_environment = True
        ctx.test_that_triggered_code_change = (ctx.current_testing_module_name, ctx.current_testing_frid)
        ctx.execution_phase = TestExecutionPhase.RETRYING_AFTER_CODE_CHANGE
        # Remember the change so that the unit tests fixer (which runs next) preserves it instead of reverting
        # it - see FixUnitTests.
        ctx.implementation_code_fixes.append({**summary, "code_diff": implementation_code_diff})
        return self.IMPLEMENTATION_CODE_UPDATED, None

    def _handle_conflict(self, render_context: RenderContext, fix: ApprovedFix) -> None:
        ctx = render_context.conformance_tests_running_context
        failing_reason = (
            "CONFLICTING_REQUIREMENTS" if self._is_regression(render_context) else "CONFLICTING_ACCEPTANCE_TESTS"
        )
        if ctx.conflicting_requirement_count >= 1:
            # Declared before, a fix did not resolve it, and the reviewer agreed again: the server reports it as
            # the ConflictingRequirements error, which ends the render.
            render_context.codeplain_api.report_conflicting_requirements(
                render_context.frid_context.frid,
                str(ctx.current_testing_frid),
                render_context.plain_source_tree,
                render_context.module_name,
                ctx.current_testing_module_name,
                render_context.get_required_modules_functionalities(),
                failing_reason,
                fix.args.get("conflict_reason", ""),
                run_state=render_context.run_state,
            )
            raise InternalClientError("The server did not report the confirmed conflict.")
        ctx.conflicting_requirement_count += 1
        ctx.conflicting_module_name = ctx.current_testing_module_name
        ctx.conflicting_frid = ctx.current_testing_frid
        console.info(
            f"↻ Potential conflicting functionalities detected while fixing conformance tests "
            f"for functionality {render_context.testing_functionality_label()}.",
            color=RETRY_COLOR,
        )

    # ---------------------------------------------------------------------------------------------------------

    @staticmethod
    def _agents_for_current_tests(render_context: RenderContext) -> ConformanceFixAgents:
        """The fixer and reviewer of the failing tests; new ones when a different functionality's tests fail."""
        ctx = render_context.conformance_tests_running_context
        tested = (ctx.current_testing_module_name, str(ctx.current_testing_frid))
        if ctx.conformance_fix_agents is None or ctx.conformance_fix_agents.tested != tested:
            ctx.conformance_fix_agents = ConformanceFixAgents(
                tested=tested,
                conformance_tests_folder=FixConformanceTest._writable_conformance_tests_folder(render_context),
                readable_tests_folders=FixConformanceTest._readable_conformance_tests_folders(render_context),
            )
        return ctx.conformance_fix_agents

    @staticmethod
    def _readable_conformance_tests_folders(render_context: RenderContext) -> list[str]:
        """The existing conformance tests folders of the current module and of every module it requires."""
        module_names = [render_context.module_name] + [
            module.module_name for module in render_context.plain_module.all_required_modules
        ]
        folders: list[str] = []
        for module_name in module_names:
            folder = os.path.normpath(
                os.path.abspath(render_context.conformance_tests.get_module_conformance_tests_folder(module_name))
            )
            if os.path.isdir(folder) and folder not in folders:
                folders.append(folder)
        return folders

    @staticmethod
    def _writable_conformance_tests_folder(render_context: RenderContext) -> str:
        """The folder of the failing conformance tests. A required module's tests are fixed in their copy inside
        the current module (made here if missing), which is the copy the conformance tests then run from."""
        ctx = render_context.conformance_tests_running_context
        folder = ctx.get_current_conformance_test_folder_name()
        if render_context.module_name != ctx.current_testing_module_name:
            source, copy = render_context.conformance_tests.get_source_conformance_test_folder_name(
                render_context.module_name,
                render_context.required_modules,
                ctx.current_testing_module_name,
                folder,
            )
            if not os.path.exists(copy):
                file_utils.copy_folder_content(source, copy)
            folder = copy
        return os.path.normpath(os.path.abspath(folder))

    @staticmethod
    def _is_regression(render_context: RenderContext) -> bool:
        """Whether the failing tests belong to a functionality implemented before the current one."""
        ctx = render_context.conformance_tests_running_context
        return (
            ctx.current_testing_module_name != render_context.module_name
            or ctx.current_testing_frid != render_context.frid_context.frid
        )

    @staticmethod
    def _common_params(render_context: RenderContext, agents: ConformanceFixAgents) -> dict:
        """The task params both agents start a session with (besides the issue)."""
        ctx = render_context.conformance_tests_running_context
        frid = render_context.frid_context.frid
        specifications, _ = plain_spec.get_specifications_for_frid(render_context.plain_source_tree, frid)
        tested_text, tested_scenarios = FixConformanceTest._tested_functionality(
            ctx.current_testing_frid_specifications
        )
        is_regression = FixConformanceTest._is_regression(render_context)
        _, memory_files = MemoryManager.fetch_memory_files(render_context.memory_manager.memory_folder)
        params = {
            "definitions": "\n".join(specifications.get(plain_spec.DEFINITIONS, [])),
            "non_functional_requirements": "\n".join(specifications.get(plain_spec.NON_FUNCTIONAL_REQUIREMENTS, [])),
            "test_requirements": "\n".join(specifications.get(plain_spec.TEST_REQUIREMENTS, [])),
            "functional_requirements": FixUnitTests._functional_requirements_section(render_context, specifications),
            "linked_resources": render_context.frid_context.linked_resources,
            "module_name": render_context.module_name,
            "build_folder": os.path.normpath(os.path.abspath(render_context.build_folder)),
            "conformance_tests_folder": agents.conformance_tests_folder,
            "readable_conformance_tests_folders": agents.readable_tests_folders,
            "conformance_tests_script_content": FixUnitTests._read_script(render_context.conformance_tests_script),
            "prepare_environment_script_content": FixUnitTests._read_script(render_context.prepare_environment_script),
            "tested_functionality_id": qualified_frid(ctx.current_testing_module_name, ctx.current_testing_frid),
            "tested_functionality": tested_text,
            "is_regression": is_regression,
            "conformance_scenarios": _stringify_scenarios(tested_scenarios),
            "acceptance_tests": _stringify_scenarios(specifications.get(plain_spec.ACCEPTANCE_TESTS, [])),
            "memory_files": memory_files,
            "conformance_tests_files": _folder_files(agents.conformance_tests_folder),
        }
        if is_regression:
            params["code_diff"] = ImplementationCodeHelpers.get_code_diff(
                render_context.build_folder, render_context.plain_source_tree, frid
            )
        return params

    @staticmethod
    def _fixer_params(render_context: RenderContext, common_params: dict) -> dict:
        ctx = render_context.conformance_tests_running_context
        params = dict(common_params)
        params["file_tree"] = FixUnitTests._file_tree(render_context.build_folder)
        params["relevant_files"] = FixUnitTests._relevant_files(
            render_context.build_folder, set(render_context.frid_context.changed_files)
        )
        if ctx.conflicting_requirement_count >= 1:
            params["conflict_already_reported"] = True
        return params

    @staticmethod
    def _tested_functionality(specifications: Optional[dict | str]) -> tuple[str, list]:
        """The text and the conformance scenarios of the functionality whose tests fail. A required module's
        functionality is known only by its text."""
        if isinstance(specifications, str):
            return specifications, []
        specifications = specifications or {}
        functional_requirements = specifications.get(plain_spec.FUNCTIONAL_REQUIREMENTS) or [""]
        return functional_requirements[-1], specifications.get(plain_spec.CONFORMANCE_TESTS) or []


def _stringify_scenarios(scenarios: list) -> str:
    return "\n\n".join(f"#### Scenario `{scenario['id']}`\n\n{scenario['text']}" for scenario in scenarios or [])


def _folder_files(folder: str) -> dict[str, str]:
    """The text files of `folder` by full path, smallest first, within MAX_CONFORMANCE_TESTS_FILES_CHARS."""
    if not os.path.isdir(folder):
        return {}
    contents = file_utils.get_existing_files_content(folder, file_utils.list_all_text_files(folder))
    selected, total = {}, 0
    for name in sorted(contents, key=lambda n: (len(contents[n]), n)):
        if total + len(contents[name]) > MAX_CONFORMANCE_TESTS_FILES_CHARS:
            break
        selected[os.path.join(folder, name)] = contents[name]
        total += len(contents[name])
    return dict(sorted(selected.items()))
