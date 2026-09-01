from typing import Any

from plain2code_console import console
from render_machine.actions.base_action import BaseAction
from render_machine.implementation_code_helpers import ImplementationCodeHelpers
from render_machine.render_context import RenderContext


class DistillConformanceTestMemory(BaseAction):
    """Distills the conformance tests fix journals into durable memories once all tests pass.

    Runs exactly once per functional requirement, as the first postprocessing step. When no fix
    attempts were needed there is nothing to distill and no API call is made. Distillation is best
    effort - a failure must never fail the render, so the journals are kept for the next
    distillation instead.

    The memories land in the module's own memory store and are inherited by the next module in the
    chain when its render starts.
    """

    SUCCESSFUL_OUTCOME = "conformance_test_memory_distilled"

    def execute(self, render_context: RenderContext, _previous_action_payload: Any | None):
        journal = render_context.memory_manager.journal
        fix_journals = journal.collect_all()
        if not fix_journals:
            return self.SUCCESSFUL_OUTCOME, None

        console.info(f"Distilling conformance test fix learnings for functionality {render_context.frid_context.frid}.")

        try:
            _, existing_files_content = ImplementationCodeHelpers.fetch_existing_files(render_context.build_folder)
            _, memory_files_content = render_context.memory_manager.fetch_memory_files(
                render_context.memory_manager.memory_folder
            )

            response_files = render_context.codeplain_api.distill_conformance_test_memory(
                frid=render_context.frid_context.frid,
                plain_source_tree=render_context.plain_source_tree,
                linked_resources=render_context.frid_context.linked_resources,
                existing_files_content=existing_files_content,
                memory_files_content=memory_files_content,
                module_name=render_context.module_name,
                required_modules=render_context.get_required_modules_functionalities(),
                fix_journals=fix_journals,
                run_state=render_context.run_state,
            )

            if response_files:
                render_context.memory_manager.store_memory_files(response_files)
        except Exception as e:
            console.error(f"Failed to distill conformance test memory (keeping the journals): {e}")
            return self.SUCCESSFUL_OUTCOME, None

        journal.clear_all()
        return self.SUCCESSFUL_OUTCOME, None
