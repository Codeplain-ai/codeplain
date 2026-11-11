import os
from typing import Any, Optional

import file_utils
import plain_spec
from memory_management import MemoryManager
from plain2code_console import RETRY_COLOR, console
from plain2code_exceptions import InternalClientError
from plain2code_utils import qualified_frid
from render_machine import agent_tools
from render_machine.actions.base_action import BaseAction
from render_machine.actions.fix_unit_tests import FixUnitTests
from render_machine.conformance_fix_conversation import (
    CONFLICTING_REQUIREMENTS,
    ConformanceFixConversation,
)
from render_machine.implementation_code_helpers import ImplementationCodeHelpers
from render_machine.render_context import RenderContext
from render_machine.render_types import ConformanceFixAgents, RenderError, TestExecutionPhase

MAX_CONFORMANCE_TEST_FIX_ATTEMPTS = 20
MAX_CONFORMANCE_TEST_RERENDER_ATTEMPTS = 1
# What is recorded for the changes of a fix the reviewer did not approve (see FixConformanceTest.execute).
UNAPPROVED_FIX_ARGS = {
    "root_cause": None,
    "changes_made": "Changes the conformance tests fixer made without getting them approved by the reviewer.",
}
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
            if ctx.current_testing_module_name != render_context.module_name:
                # The failing test belongs to an earlier module. Its tests can't be written again from here: this
                # module's context holds neither that functionality's specification nor its tests folder (doing it
                # anyway crashed the render). Stop with the reason instead.
                error_msg = (
                    f"The conformance tests of functionality "
                    f"'{render_context.testing_functionality_label()}' of an earlier "
                    f"module fail after the changes made for functionality "
                    f"'{render_context.functionality_label()}', and the "
                    f"attempts to fix them are used up. Please review both specifications. "
                    f"(Render ID: {render_context.run_state.render_id})"
                )
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
        if fix is not None:
            return self._apply_outcome(render_context, conversation, fix.args, fix.changed_files)

        # No fix was approved. Its changes stay (see ConformanceFixConversation.fix) and the tests decide; changed
        # implementation code is then tested like an approved change, after the unit tests and a fresh preparation,
        # so it cannot be committed with broken unit tests or tested on an environment prepared for older code.
        changed_files = conversation.unapproved_changes()
        if any(path.startswith(conversation.build_folder + os.sep) for path in changed_files):
            console.warning(
                "No fix was approved; the implementation code the fixer changed is kept and tested: the unit tests "
                "first, then the conformance tests."
            )
            return self._apply_outcome(render_context, conversation, UNAPPROVED_FIX_ARGS, changed_files)
        journal.record_fix(
            ctx.current_testing_module_name, ctx.current_testing_frid, None, [], "IMPLEMENTATION_CODE", None, None
        )
        return self.IMPLEMENTATION_CODE_NOT_UPDATED, None

    # ---------------------------------------------------------------------------------------------------------

    def _apply_outcome(
        self,
        render_context: RenderContext,
        conversation: ConformanceFixConversation,
        args: dict,
        changed_files: dict[str, Optional[str]],
    ):
        """Record the fix (the fixer's submit_fix `args` and the files it changed) and pick the next step: the unit
        tests when the implementation code changed, otherwise the conformance tests right away. A conflict confirmed
        a second time ends the render."""
        ctx = render_context.conformance_tests_running_context
        build_folder = conversation.build_folder
        implementation_files = {path for path in changed_files if path.startswith(build_folder + os.sep)}
        code_diff = conversation.diff(changed_files)
        implementation_code_diff = {
            name: diff for name, diff in code_diff.items() if os.path.join(build_folder, name) in implementation_files
        }
        summary = {"hypothesis": args.get("root_cause"), "approach": args.get("changes_made")}

        if args.get("failing_reason") == CONFLICTING_REQUIREMENTS:
            self._handle_conflict(render_context, args)

        console.print_files(
            "Files changed while fixing conformance tests:",
            build_folder,
            {conversation.display_name(path): "" for path in sorted(changed_files)},
            style=console.OUTPUT_STYLE,
        )
        target = "IMPLEMENTATION_CODE" if implementation_files else "CONFORMANCE_TESTS"
        render_context.memory_manager.journal.record_fix(
            ctx.current_testing_module_name,
            ctx.current_testing_frid,
            summary,
            sorted(conversation.display_name(path) for path in changed_files),
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
        ctx.implementation_code_fixes.append(
            {
                **summary,
                "code_diff": implementation_code_diff,
                "tested": (ctx.current_testing_module_name, str(ctx.current_testing_frid)),
            }
        )
        return self.IMPLEMENTATION_CODE_UPDATED, None

    def _handle_conflict(self, render_context: RenderContext, args: dict) -> None:
        ctx = render_context.conformance_tests_running_context
        failing_reason = (
            "CONFLICTING_REQUIREMENTS" if self._is_regression(render_context) else "CONFLICTING_ACCEPTANCE_TESTS"
        )
        conflicting_module, conflicting_frid = ctx.current_testing_module_name, str(ctx.current_testing_frid)
        other_tested = self._other_functionality_of_conflict(render_context)
        if other_tested is not None:
            # The functionality has no acceptance tests to conflict with: its tests conflict with the functionality
            # whose tests an earlier fix of this phase made pass (see _other_functionalities_fixes).
            failing_reason = "CONFLICTING_REQUIREMENTS"
            conflicting_module, conflicting_frid = other_tested
        if ctx.conflicting_requirement_count >= 1:
            # Declared before, a fix did not resolve it, and the reviewer agreed again: the server reports it as
            # the ConflictingRequirements error, which ends the render.
            render_context.codeplain_api.report_conflicting_requirements(
                render_context.frid_context.frid,
                conflicting_frid,
                render_context.plain_source_tree,
                render_context.module_name,
                conflicting_module,
                render_context.get_required_modules_functionalities(),
                failing_reason,
                args.get("conflict_reason", ""),
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

    @staticmethod
    def _other_functionality_of_conflict(render_context: RenderContext) -> Optional[tuple[str, str]]:
        """The functionality a conflict declared for the current functionality's own tests is with, when that
        functionality has no acceptance tests: the one whose tests the most recent fix of this phase for other tests
        made pass. None for regression tests, with acceptance tests, or without such a fix."""
        if FixConformanceTest._is_regression(render_context):
            return None
        specifications, _ = plain_spec.get_specifications_for_frid(
            render_context.plain_source_tree, render_context.frid_context.frid
        )
        if specifications.get(plain_spec.ACCEPTANCE_TESTS):
            return None
        ctx = render_context.conformance_tests_running_context
        tested = (ctx.current_testing_module_name, str(ctx.current_testing_frid))
        for fix in reversed(ctx.implementation_code_fixes):
            if fix.get("tested") and tuple(fix["tested"]) != tested:
                module_name, frid = fix["tested"]
                return module_name, frid
        return None

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
                plain_modules_folder=agent_tools.rendered_modules_folder(render_context),
                writable_module_tests_folder=(
                    os.path.normpath(
                        os.path.abspath(
                            render_context.conformance_tests.get_module_conformance_tests_folder(
                                render_context.module_name
                            )
                        )
                    )
                    if FixConformanceTest._is_regression(render_context)
                    else ""
                ),
            )
            if FixConformanceTest._is_regression(render_context):
                ctx.conformance_fix_agents.other_tests_folders = FixConformanceTest._other_tests_folders(
                    render_context, ctx.conformance_fix_agents.conformance_tests_folder
                )
        return ctx.conformance_fix_agents

    @staticmethod
    def _readable_conformance_tests_folders(render_context: RenderContext) -> list[str]:
        """The existing conformance tests folders of the current module and of every module it requires that lie
        outside the rendered modules folder (those inside it are readable with it)."""
        root = agent_tools.rendered_modules_folder(render_context)
        module_names = [render_context.module_name] + [
            module.module_name for module in render_context.plain_module.all_required_modules
        ]
        folders: list[str] = []
        for module_name in module_names:
            folder = os.path.normpath(
                os.path.abspath(render_context.conformance_tests.get_module_conformance_tests_folder(module_name))
            )
            if os.path.isdir(folder) and folder not in folders and not folder.startswith(root + os.sep):
                folders.append(folder)
        return folders

    @staticmethod
    def _writable_conformance_tests_folder(render_context: RenderContext) -> str:
        """The folder of the failing conformance tests. A required module's tests are fixed in their copy inside
        the current module (made here if missing), which is the copy the conformance tests then run from. The copies
        of that module's other functionalities' tests are made too, so a fix of a cause they share can reach them
        (they run from their copies later)."""
        ctx = render_context.conformance_tests_running_context
        folder = ctx.get_current_conformance_test_folder_name()
        if render_context.module_name == ctx.current_testing_module_name:
            return os.path.normpath(os.path.abspath(folder))
        testing_module = ctx.current_testing_module_name
        other_folders = [
            entry.get("folder_name")
            for entry in render_context.conformance_tests.get_conformance_tests_json(testing_module).values()
            if entry.get("folder_name")
        ]
        writable = folder
        for tests_folder in [folder, *other_folders]:
            source, copy = render_context.conformance_tests.get_source_conformance_test_folder_name(
                render_context.module_name, render_context.required_modules, testing_module, tests_folder
            )
            if not os.path.exists(copy) and os.path.isdir(source):
                file_utils.copy_folder_content(source, copy)
            if tests_folder == folder:
                writable = copy
        return os.path.normpath(os.path.abspath(writable))

    @staticmethod
    def _other_tests_folders(render_context: RenderContext, tests_folder: str) -> list[str]:
        """The existing folders of the other functionalities' conformance tests the failing tests' module has, where
        a regression fix runs them from: the current module's own folders, or a required module's copies in it."""
        ctx = render_context.conformance_tests_running_context
        testing_module = ctx.current_testing_module_name
        if testing_module == render_context.module_name:
            folders = [entry.get("folder_name") for entry in ctx.get_conformance_tests_json(testing_module).values()]
        else:
            folders = [
                render_context.conformance_tests.get_source_conformance_test_folder_name(
                    render_context.module_name, render_context.required_modules, testing_module, entry["folder_name"]
                )[1]
                for entry in render_context.conformance_tests.get_conformance_tests_json(testing_module).values()
                if entry.get("folder_name")
            ]
        normalized = [os.path.normpath(os.path.abspath(f)) for f in folders if f]
        return sorted({f for f in normalized if os.path.isdir(f) and f != tests_folder})

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
            **agent_tools.modules_layout_params(render_context),
            "writable_module_tests_folder": agents.writable_module_tests_folder,
            "other_conformance_tests_folders": agents.other_tests_folders,
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
        other_fixes = FixConformanceTest._other_functionalities_fixes(render_context)
        if other_fixes:
            params["other_functionalities_fixes"] = other_fixes
        return params

    @staticmethod
    def _other_functionalities_fixes(render_context: RenderContext) -> list[dict]:
        """The implementation changes made earlier in this conformance phase to make another functionality's tests
        pass, oldest first. A fixer that undoes one makes those tests fail again, and the two fixes then alternate,
        so the fixer is shown them and told to declare the conflict instead."""
        ctx = render_context.conformance_tests_running_context
        tested = (ctx.current_testing_module_name, str(ctx.current_testing_frid))
        return [
            {
                "functionality": qualified_frid(*fix["tested"]),
                "hypothesis": fix.get("hypothesis"),
                "approach": fix.get("approach"),
                "code_diff": fix.get("code_diff") or {},
            }
            for fix in ctx.implementation_code_fixes
            if fix.get("tested") and tuple(fix["tested"]) != tested
        ]

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
