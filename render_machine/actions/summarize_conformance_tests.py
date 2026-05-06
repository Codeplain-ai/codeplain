from typing import Any

from plain2code_console import console
from render_machine.actions.base_action import BaseAction
from render_machine.render_context import RenderContext


class SummarizeConformanceTests(BaseAction):
    SUCCESSFUL_OUTCOME = "conformance_tests_summarized"

    def execute(self, render_context: RenderContext, _previous_action_payload: Any | None):
        console.info(f"Summarizing conformance tests for functionality {render_context.frid_context.frid}.")

        _, existing_conformance_test_files_content = (
            render_context.conformance_tests.fetch_existing_conformance_test_files(
                render_context.module_name,
                render_context.required_modules,
                render_context.conformance_tests_running_context.current_testing_module_name,
                render_context.conformance_tests_running_context.get_current_conformance_test_folder_name(),
            )
        )

        summary = render_context.codeplain_api.summarize_finished_conformance_tests(
            frid=render_context.frid_context.frid,
            plain_source_tree=render_context.plain_source_tree,
            linked_resources=render_context.frid_context.linked_resources,
            conformance_test_files_content=existing_conformance_test_files_content,
            module_name=render_context.module_name,
            required_modules=render_context.get_required_modules_functionalities(),
            run_state=render_context.run_state,
        )

        render_context.conformance_tests_running_context.set_conformance_tests_summary(summary)

        return self.SUCCESSFUL_OUTCOME, None
