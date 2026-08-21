from typing import Any

import diff_utils
import file_utils
from memory_management import MemoryManager
from plain2code_console import RETRY_COLOR, console
from plain2code_exceptions import InternalClientError
from render_machine.actions.base_action import BaseAction
from render_machine.implementation_code_helpers import ImplementationCodeHelpers
from render_machine.render_context import RenderContext
from render_machine.render_types import RenderError


class FixModuleConformanceTest(BaseAction):
    """Fix a failing module conformance test suite.

    The fix is reasoned about with every functionality of the module in context, together with the
    map of which planned test covers which functionality, so that a fix made for one functionality is
    checked against the others.

    Unlike the per-functionality fix loop, there is no "throw the suite away and regenerate it" path:
    a module suite is too expensive to discard, and the tests that already pass carry the coverage of
    the functionalities they were planned for. The fix budget scales with the number of
    functionalities instead (see RenderContext.get_max_module_conformance_test_fix_attempts).
    """

    IMPLEMENTATION_CODE_NOT_UPDATED = "module_implementation_code_not_updated"
    IMPLEMENTATION_CODE_UPDATED = "module_implementation_code_updated"
    LIMIT_EXCEEDED_OUTCOME = "module_conformance_test_fix_limit_exceeded"

    ISSUE_REASON_CODE_CONFORMANCE_TESTS = 0
    ISSUE_REASON_CODE_IMPLEMENTATION_CODE = 1
    ISSUE_REASON_CODE_CONFLICTING_REQUIREMENTS = 2
    ISSUE_REASON_CODE_CONFLICTING_ACCEPTANCE_TESTS = 3

    def execute(self, render_context: RenderContext, previous_action_payload: Any | None):
        ctx = render_context.module_conformance_tests_running_context
        ctx.fix_attempts += 1

        max_fix_attempts = render_context.get_max_module_conformance_test_fix_attempts()
        if ctx.fix_attempts >= max_fix_attempts:
            error_msg = (
                f"The renderer was unable to produce an implementation that passes the conformance tests of module "
                f"'{render_context.module_name}' after {max_fix_attempts} attempts. Please review and rewrite the "
                f"specification. (Render ID: {render_context.run_state.render_id})"
            )
            render_context.last_error_message = error_msg
            return self.LIMIT_EXCEEDED_OUTCOME, RenderError.encode(message=error_msg).to_payload()

        suite = ctx.current_suite

        console.info(f"Running conformance tests attempt {ctx.fix_attempts + 1}.")
        console.info(f"Fixing conformance tests of module {suite.module_name}.")

        if not previous_action_payload.get("previous_conformance_tests_issue"):
            raise InternalClientError(
                "Internal client error: Previous action payload does not contain previous conformance tests issue."
            )
        previous_conformance_tests_issue = previous_action_payload["previous_conformance_tests_issue"]

        ctx.previous_conformance_tests_issue_old = previous_conformance_tests_issue
        ctx.previous_conformance_tests_issue_module = suite.module_name

        existing_files, existing_files_content = ImplementationCodeHelpers.fetch_existing_files(
            render_context.build_folder
        )
        _, memory_files_content = MemoryManager.fetch_memory_files(render_context.memory_manager.memory_folder)
        (
            existing_conformance_test_files,
            existing_conformance_test_files_content,
        ) = render_context.conformance_tests.fetch_existing_conformance_test_files(
            render_context.module_name,
            render_context.required_modules,
            suite.module_name,
            suite.require_folder_name(),
        )
        module_code_diff = ImplementationCodeHelpers.get_module_code_diff(render_context.build_folder)

        if ctx.conflicting_module_name != suite.module_name:
            # The conflict counter tracks one suite at a time, the same way the per-functionality loop
            # tracks one functionality at a time.
            ctx.conflicting_requirement_count = 0

        console.print_files(
            "Implementation files sent as input for fixing the module conformance tests issues:",
            render_context.build_folder,
            existing_files_content,
            style=console.INPUT_STYLE,
        )
        console.print_files(
            "Conformance tests files sent as input for fixing the module conformance tests issues:",
            suite.require_folder_name(),
            existing_conformance_test_files_content,
            style=console.INPUT_STYLE,
        )

        [issue_reason_code, response_files] = render_context.codeplain_api.fix_module_conformance_tests_issue(
            render_context.plain_source_tree,
            render_context.all_linked_resources,
            existing_files_content,
            memory_files_content,
            render_context.module_name,
            suite.module_name,
            render_context.get_required_modules_functionalities(),
            module_code_diff,
            existing_conformance_test_files_content,
            ctx.get_conformance_tests_coverage(),
            previous_conformance_tests_issue,
            ctx.fix_attempts,
            suite.require_folder_name(),
            ctx.conformance_tests_plan_summary,
            ctx.conflicting_requirement_count,
            run_state=render_context.run_state,
        )

        if issue_reason_code in (
            self.ISSUE_REASON_CODE_CONFLICTING_REQUIREMENTS,
            self.ISSUE_REASON_CODE_CONFLICTING_ACCEPTANCE_TESTS,
        ):
            ctx.conflicting_requirement_count += 1
            ctx.conflicting_module_name = suite.module_name
            console.info(
                f"↻ Potential conflicting functionalities detected while fixing the conformance tests "
                f"of module {suite.module_name}.",
                color=RETRY_COLOR,
            )

        if issue_reason_code == self.ISSUE_REASON_CODE_CONFORMANCE_TESTS:
            render_context.conformance_tests.store_conformance_tests_files(
                render_context.module_name,
                render_context.required_modules,
                suite.module_name,
                suite.require_folder_name(),
                response_files,
                existing_conformance_test_files,
            )
            ctx.code_diff_files = diff_utils.get_code_diff(response_files, existing_conformance_test_files_content)

            return self.IMPLEMENTATION_CODE_NOT_UPDATED, None

        if not response_files:
            return self.IMPLEMENTATION_CODE_NOT_UPDATED, None

        file_utils.store_response_files(render_context.build_folder, response_files, existing_files)
        ctx.code_diff_files = diff_utils.get_code_diff(response_files, existing_files_content)
        console.print_files(
            "Files fixed:",
            render_context.build_folder,
            response_files,
            style=console.OUTPUT_STYLE,
        )

        # A change to the implementation code can regress a suite that has already passed, so the
        # whole sweep starts over once the unit tests agree with the change again.
        ctx.implementation_code_updated = True
        ctx.should_prepare_testing_environment = True
        ctx.restart_suites()
        ctx.fix_attempts = 0

        return self.IMPLEMENTATION_CODE_UPDATED, None
