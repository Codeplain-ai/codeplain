from typing import Any

import file_utils
from memory_management import MemoryManager
from plain2code_console import console
from render_machine.actions.base_action import BaseAction
from render_machine.implementation_code_helpers import ImplementationCodeHelpers
from render_machine.render_context import RenderContext


class PlanModuleConformanceTests(BaseAction):
    """Devise the plan for the module's conformance test suite.

    One plan covers every functionality of the module, and it is devised afresh on every render of
    the module: reaching this phase means the functionalities were just implemented, so a suite left
    behind by an earlier render is discarded rather than partially reused.
    """

    SUCCESSFUL_OUTCOME = "module_conformance_tests_planned"

    def execute(self, render_context: RenderContext, _previous_action_payload: Any | None):
        ctx = render_context.module_conformance_tests_running_context

        if ctx.own_suite.exists():
            # Reaching this phase means the module was (re-)rendered, so the previous suite was
            # planned against functionalities that have since been re-implemented. The suite is
            # planned afresh rather than partially reused, so that its coverage matches the code.
            console.info(
                f"Discarding the previous conformance tests of module {render_context.module_name} "
                f"in {ctx.own_suite.folder_name} and planning them afresh."
            )
            file_utils.delete_folder(ctx.own_suite.folder_name)
            ctx.own_suite.folder_name = None

        console.info(f"Planning conformance tests covering all functionalities of module {render_context.module_name}.")

        _, existing_files_content = ImplementationCodeHelpers.fetch_existing_files(render_context.build_folder)
        _, memory_files_content = MemoryManager.fetch_memory_files(render_context.memory_manager.memory_folder)

        conformance_tests_plan, conformance_tests_plan_summary, uncovered_frids, number_of_batches = (
            render_context.codeplain_api.devise_module_conformance_tests_plan(
                render_context.plain_source_tree,
                render_context.all_linked_resources,
                existing_files_content,
                memory_files_content,
                render_context.module_name,
                render_context.get_required_modules_functionalities(),
                render_context.get_required_modules_conformance_tests(),
                run_state=render_context.run_state,
            )
        )

        ctx.conformance_tests_plan = conformance_tests_plan
        ctx.conformance_tests_plan_summary = conformance_tests_plan_summary
        ctx.uncovered_frids = uncovered_frids
        ctx.number_of_batches = number_of_batches
        ctx.batches_rendered = 0

        planned_tests = (conformance_tests_plan or {}).get("test_summary") or []
        console.info(f"Planned {len(planned_tests)} conformance tests for module {render_context.module_name}:")
        console.print_list(
            [self._describe_planned_test(planned_test) for planned_test in planned_tests],
            style=console.INFO_STYLE,
        )

        if uncovered_frids:
            # The plan is allowed to leave a functionality to its acceptance tests, but an
            # unintentionally uncovered functionality would otherwise be invisible.
            console.info(
                "Functionalities not covered by any planned conformance test: " + ", ".join(uncovered_frids) + "."
            )

        return self.SUCCESSFUL_OUTCOME, None

    def _describe_planned_test(self, planned_test: dict) -> str:
        test_name = planned_test.get("test_name", "")
        covers = planned_test.get("covers_functionalities") or []
        if not covers:
            return test_name

        return f"{test_name} (functionalities {', '.join(covers)})"
