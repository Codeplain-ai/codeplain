from typing import Any

import file_utils
from memory_management import MemoryManager
from plain2code_console import console
from render_machine.actions.base_action import BaseAction
from render_machine.implementation_code_helpers import ImplementationCodeHelpers
from render_machine.render_context import RenderContext


class RenderModuleConformanceTests(BaseAction):
    """Implement one batch of the module conformance tests plan.

    A suite covering a whole module does not fit into a single LLM response, so the plan is
    implemented a batch at a time into the same folder. The action renders one batch per invocation
    and reports whether more remain, so the state machine stays responsive to pause and stop between
    batches.
    """

    BATCH_RENDERED_OUTCOME = "module_conformance_tests_batch_rendered"
    SUITE_RENDERED_OUTCOME = "module_conformance_tests_suite_rendered"

    def execute(self, render_context: RenderContext, _previous_action_payload: Any | None):
        ctx = render_context.module_conformance_tests_running_context

        if not ctx.has_more_batches_to_render():
            return self.SUITE_RENDERED_OUTCOME, None

        batch_index = ctx.batches_rendered
        conformance_tests_folder_name = ctx.own_suite.folder_name
        if conformance_tests_folder_name is None:
            conformance_tests_folder_name = render_context.conformance_tests.build_module_suite_folder_name(
                render_context.module_name
            )

        console.info(
            f"Implementing conformance tests for module {render_context.module_name} "
            f"(batch {batch_index + 1} of {ctx.number_of_batches})."
        )

        _, existing_files_content = ImplementationCodeHelpers.fetch_existing_files(render_context.build_folder)
        _, memory_files_content = MemoryManager.fetch_memory_files(render_context.memory_manager.memory_folder)

        existing_conformance_tests_files, existing_conformance_tests_files_content = (
            render_context.conformance_tests.fetch_existing_conformance_test_files(
                render_context.module_name,
                render_context.required_modules,
                render_context.module_name,
                conformance_tests_folder_name,
            )
        )

        console.print_files(
            "Files sent as input for generating the module conformance tests:",
            render_context.build_folder,
            existing_files_content,
            style=console.INPUT_STYLE,
        )

        response_files = render_context.codeplain_api.render_module_conformance_tests(
            render_context.plain_source_tree,
            render_context.all_linked_resources,
            existing_files_content,
            memory_files_content,
            render_context.module_name,
            render_context.get_required_modules_functionalities(),
            conformance_tests_folder_name,
            existing_conformance_tests_files_content,
            ctx.conformance_tests_plan,
            batch_index,
            run_state=render_context.run_state,
        )

        file_utils.store_response_files(conformance_tests_folder_name, response_files, existing_conformance_tests_files)

        console.print_files(
            f"Conformance test files in folder {conformance_tests_folder_name} generated:",
            conformance_tests_folder_name,
            response_files,
            style=console.OUTPUT_STYLE,
        )

        ctx.batches_rendered += 1
        # The suite now exists on disk, so later steps can find it and the testing environment that
        # was prepared before the suite changed is stale.
        ctx.own_suite.folder_name = conformance_tests_folder_name
        ctx.should_prepare_testing_environment = True

        if ctx.has_more_batches_to_render():
            return self.BATCH_RENDERED_OUTCOME, None

        return self.SUITE_RENDERED_OUTCOME, None
