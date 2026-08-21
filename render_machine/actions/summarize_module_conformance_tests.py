from typing import Any

from plain2code_console import console
from render_machine.actions.base_action import BaseAction
from render_machine.render_context import RenderContext


class SummarizeModuleConformanceTests(BaseAction):
    """Summarize the module's conformance test suite.

    The summary is what keeps the modules built on top of this one from re-testing what it already
    covers, so it is stored with the suite (see CommitModuleConformanceTestsChanges).
    """

    SUCCESSFUL_OUTCOME = "module_conformance_tests_summarized"

    def execute(self, render_context: RenderContext, _previous_action_payload: Any | None):
        ctx = render_context.module_conformance_tests_running_context

        console.info(f"Summarizing the conformance tests of module {render_context.module_name}.")

        _, existing_conformance_test_files_content = (
            render_context.conformance_tests.fetch_existing_conformance_test_files(
                render_context.module_name,
                render_context.required_modules,
                render_context.module_name,
                ctx.own_suite.require_folder_name(),
            )
        )

        ctx.test_summary = render_context.codeplain_api.summarize_module_conformance_tests(
            render_context.plain_source_tree,
            render_context.all_linked_resources,
            existing_conformance_test_files_content,
            render_context.module_name,
            render_context.get_required_modules_functionalities(),
            run_state=render_context.run_state,
        )

        return self.SUCCESSFUL_OUTCOME, None
