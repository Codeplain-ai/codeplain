"""Regression testing loop of the conformance tests, for a rerendered functionality.

The rerendered functionality is tested in the initial phase, so the regression loop moves past it.
Getting the next test moves the frid of the running context in place, and there is no frid after the
last one, so the loop must not advance when the rerendered functionality is the last one.
"""

from render_machine import triggers
from render_machine.render_context import RenderContext
from render_machine.render_types import ConformanceTestsRunningContext
from render_machine.render_types import TestExecutionPhase as ExecutionPhase

PLAIN_SOURCE_TREE = {"functional specs": [{"markdown": f"- fr{index}"} for index in range(1, 5)]}


class _RecordingMachine:
    def __init__(self):
        self.dispatched = []

    def dispatch(self, trigger):
        self.dispatched.append(trigger)


def _render_context(current_frid, frid_being_implemented, frids_with_tests):
    """A RenderContext carrying only what _handle_regression_testing reads.

    _get_next_test_to_run is deliberately NOT stubbed: the real one moves the frid of the running
    context in place, which is the behaviour these tests need to cover.
    """
    context = ConformanceTestsRunningContext(
        current_testing_module_name="cli",
        current_testing_frid=current_frid,
        fix_attempts=0,
        conformance_tests_json={frid: {"folder_name": f"tests_{frid}"} for frid in frids_with_tests},
        conformance_tests_render_attempts=0,
        current_testing_frid_specifications=None,
        should_prepare_testing_environment=False,
        frid_being_implemented=frid_being_implemented,
    )
    context.execution_phase = ExecutionPhase.RUNNING_REGRESSION

    render_context = RenderContext.__new__(RenderContext)
    render_context.is_rerender = True
    render_context.module_name = "cli"
    render_context.required_modules = []
    render_context.plain_source_tree = PLAIN_SOURCE_TREE
    render_context.machine = _RecordingMachine()
    render_context.conformance_tests_running_context = context
    return render_context


def test_rerender_of_last_functionality_ends_the_regression_run():
    """The regression loop reaches the rerendered last functionality. It must complete there and keep
    the frid, because the postprocessing steps look their folder up by it."""
    render_context = _render_context(current_frid="3", frid_being_implemented="4", frids_with_tests=["3", "4"])

    render_context._handle_regression_testing()

    context = render_context.conformance_tests_running_context
    assert render_context.machine.dispatched == [triggers.MARK_ALL_CONFORMANCE_TESTS_PASSED]
    assert context.execution_phase is ExecutionPhase.COMPLETED
    # The frid must survive. Moving past the last functionality would leave it as None, and the
    # conformance test folder lookup would then raise a KeyError of None.
    assert context.current_testing_frid == "4"


def test_rerender_of_earlier_functionality_advances_past_it():
    """When the rerendered functionality is not the last one, the loop moves on to the next test."""
    render_context = _render_context(current_frid="1", frid_being_implemented="2", frids_with_tests=["1", "2", "3"])

    render_context._handle_regression_testing()

    context = render_context.conformance_tests_running_context
    assert context.current_testing_frid == "3"
    assert context.current_testing_frid_specifications is not None
    assert render_context.machine.dispatched == [triggers.MARK_CONFORMANCE_TESTS_READY]
