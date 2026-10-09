"""Regression testing loop of the conformance tests, for a regenerated functionality.

The regenerated functionality is tested in the initial phase, so the regression loop moves past it.
Getting the next test moves the frid of the running context in place, and there is no frid after the
last one, so the loop must not advance when the regenerated functionality is the last one.
"""

import plain_spec
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


def _render_context(current_frid, frid_being_implemented, frids_with_tests, testing_module_name="cli"):
    """A RenderContext carrying only what _handle_regression_testing reads.

    _get_next_test_to_run is deliberately NOT stubbed: the real one moves the frid of the running
    context in place, which is the behaviour these tests need to cover.
    """
    context = ConformanceTestsRunningContext(
        current_testing_module_name=testing_module_name,
        current_testing_frid=current_frid,
        fix_attempts=0,
        conformance_tests_json={
            frid: {"folder_name": f"tests_{frid}", "functional_requirement": f"- fr{frid}"} for frid in frids_with_tests
        },
        conformance_tests_render_attempts=0,
        current_testing_frid_specifications=None,
        should_prepare_testing_environment=False,
        frid_being_implemented=frid_being_implemented,
    )
    context.execution_phase = ExecutionPhase.RUNNING_REGRESSION

    render_context = RenderContext.__new__(RenderContext)
    render_context.is_regenerate = True
    render_context.module_name = "cli"
    render_context.required_modules = []
    render_context.plain_source_tree = PLAIN_SOURCE_TREE
    render_context.machine = _RecordingMachine()
    render_context.conformance_tests_running_context = context
    return render_context


def test_regenerate_of_last_functionality_ends_the_regression_run():
    """The regression loop reaches the regenerated last functionality. It must complete there and keep
    the frid, because the postprocessing steps look their folder up by it."""
    render_context = _render_context(current_frid="3", frid_being_implemented="4", frids_with_tests=["3", "4"])

    render_context._handle_regression_testing()

    context = render_context.conformance_tests_running_context
    assert render_context.machine.dispatched == [triggers.MARK_ALL_CONFORMANCE_TESTS_PASSED]
    assert context.execution_phase is ExecutionPhase.COMPLETED
    # The frid must survive. Moving past the last functionality would leave it as None, and the
    # conformance test folder lookup would then raise a KeyError of None.
    assert context.current_testing_frid == "4"


def test_regenerate_of_earlier_functionality_advances_past_it():
    """When the regenerated functionality is not the last one, the loop moves on to the next test."""
    render_context = _render_context(current_frid="1", frid_being_implemented="2", frids_with_tests=["1", "2", "3"])

    render_context._handle_regression_testing()

    context = render_context.conformance_tests_running_context
    assert context.current_testing_frid == "3"
    assert context.current_testing_frid_specifications is not None
    assert render_context.machine.dispatched == [triggers.MARK_CONFORMANCE_TESTS_READY]


def test_regenerated_functionality_number_of_a_required_module_still_runs():
    """Only the module that owns the regenerated functionality skips it in the regression run. A
    required module that happens to have a functionality with the same number must still be tested."""
    render_context = _render_context(
        current_frid="1",
        frid_being_implemented="2",
        frids_with_tests=["1", "2", "3"],
        testing_module_name="ledger",
    )

    render_context._handle_regression_testing()

    context = render_context.conformance_tests_running_context
    assert context.current_testing_module_name == "ledger"
    assert context.current_testing_frid == "2"
    assert render_context.machine.dispatched == [triggers.MARK_CONFORMANCE_TESTS_READY]


def test_regression_run_ends_when_the_next_functionality_has_no_conformance_tests():
    """A project that was rendered only in part has functionalities without conformance tests. The
    regression run must end there, on the functionality being implemented, and report that all
    conformance tests passed."""
    # The spec file holds four functionalities, but only 1, 2 and 3 were ever given conformance
    # tests. Functionality 2 is the regenerated one, and 3 has just been tested.
    render_context = _render_context(current_frid="3", frid_being_implemented="2", frids_with_tests=["1", "3", "2"])

    render_context._handle_regression_testing()

    context = render_context.conformance_tests_running_context
    assert render_context.machine.dispatched == [triggers.MARK_ALL_CONFORMANCE_TESTS_PASSED]
    assert context.execution_phase is ExecutionPhase.COMPLETED
    # The postprocessing steps look the conformance test folder up by this frid, so it must be the
    # functionality being implemented and not the one without conformance tests.
    assert context.current_testing_frid == "2"
    assert context.current_testing_frid_specifications is not None


def test_regression_run_tests_the_implemented_functionality_again_when_code_changed():
    """When the implementation code changed during the run, the functionality being implemented is
    tested once more before the run ends."""
    render_context = _render_context(current_frid="3", frid_being_implemented="2", frids_with_tests=["1", "3", "2"])
    render_context.conformance_tests_running_context.code_changed_during_regression = True

    render_context._handle_regression_testing()

    context = render_context.conformance_tests_running_context
    assert render_context.machine.dispatched == [triggers.MARK_CONFORMANCE_TESTS_READY]
    assert context.execution_phase is ExecutionPhase.COMPLETED
    assert context.current_testing_frid == "2"


def test_acceptance_test_of_another_functionality_is_not_read_past_its_end():
    """The completed count belongs to the functionality being implemented. Another functionality can
    have fewer acceptance tests, so the count is not a valid position in its list."""
    context = _render_context(
        current_frid="1", frid_being_implemented="2", frids_with_tests=["1"]
    ).conformance_tests_running_context
    context.current_testing_frid_specifications = {plain_spec.ACCEPTANCE_TESTS: ["- the only acceptance test"]}
    context.acceptance_tests_completed = 2

    assert context.get_current_acceptance_test() is None
