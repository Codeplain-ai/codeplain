from render_machine.actions.prepare_testing_environment import PrepareTestingEnvironment
from render_machine.render_context import RenderContext


class PrepareModuleTestingEnvironment(PrepareTestingEnvironment):
    """Prepare the testing environment for the module-scoped conformance testing phase.

    Same behaviour as PrepareTestingEnvironment, reading the flag from the module conformance
    context. Distinct outcome constants are required because the outcome-to-trigger map is flat, so
    reusing the parent's outcomes would route the module phase into the per-functionality states.
    """

    SUCCESSFUL_OUTCOME = "module_testing_environment_prepared"
    FAILED_OUTCOME = "module_testing_environment_preparation_failed"

    def _get_running_context(self, render_context: RenderContext):
        return render_context.module_conformance_tests_running_context
