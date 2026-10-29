"""A conformance fix that changes implementation code must not cancel the acceptance scenarios.

The fix moves the execution phase to RETRYING_AFTER_CODE_CHANGE. When the test then passes, the
render must go back to the testing phase of the functionality while acceptance scenarios remain,
because that phase holds the only code that renders them. Regression starts after the last one.
"""

from types import SimpleNamespace

import pytest

import plain_spec
from render_machine.render_context import RenderContext
from render_machine.render_types import (
    AcceptanceTestPhase,
    ConformanceTestsRunningContext,
)
from render_machine.render_types import TestExecutionPhase as ExecutionPhase

MODULE = "vault-cli"
FRID = "get-command"
SCENARIOS = [
    {"id": "get-with-an-id-that-is-not-in-the-vault", "text": "..."},
    {"id": "get-with-a-wrong-master-password", "text": "..."},
    {"id": "get-from-a-vault-that-is-not-there", "text": "..."},
]


def make_render_context(acceptance_scenarios, acceptance_test_phase, acceptance_tests_completed):
    """A render context stopped right after a code fix, with its test passing again."""
    render_context = RenderContext.__new__(RenderContext)
    render_context.module_name = MODULE
    render_context.frid_context = SimpleNamespace(
        frid=FRID, specifications={plain_spec.ACCEPTANCE_TESTS: acceptance_scenarios}
    )

    ctx = ConformanceTestsRunningContext(
        current_testing_module_name=MODULE,
        current_testing_frid=FRID,
        fix_attempts=0,
        conformance_tests_json={FRID: {"folder_name": "get_command"}},
        conformance_tests_render_attempts=0,
        current_testing_frid_specifications={plain_spec.ACCEPTANCE_TESTS: acceptance_scenarios},
        should_prepare_testing_environment=False,
        frid_being_implemented=FRID,
    )
    ctx.execution_phase = ExecutionPhase.RETRYING_AFTER_CODE_CHANGE
    ctx.test_that_triggered_code_change = (MODULE, FRID)
    ctx.acceptance_test_phase = acceptance_test_phase
    ctx.acceptance_tests_completed = acceptance_tests_completed
    render_context.conformance_tests_running_context = ctx

    return render_context


def test_a_scenario_that_remains_gets_a_test_after_a_code_fix():
    """The first scenario has a test, the fix changed code, two scenarios remain."""
    render_context = make_render_context(SCENARIOS, AcceptanceTestPhase.IN_PROGRESS, 1)

    render_context.start_conformance_tests_for_frid()

    ctx = render_context.conformance_tests_running_context
    # The render asks for the second scenario instead of moving on to regression.
    assert ctx.acceptance_tests_completed == 2
    assert ctx.execution_phase == ExecutionPhase.TESTING_CURRENT_FRID
    assert ctx.acceptance_test_phase == AcceptanceTestPhase.IN_PROGRESS


def test_every_scenario_is_reached_when_a_code_fix_follows_each_one():
    """A code fix after every scenario still leaves no scenario without a test."""
    render_context = make_render_context(SCENARIOS, AcceptanceTestPhase.IN_PROGRESS, 1)
    ctx = render_context.conformance_tests_running_context

    for _ in range(len(SCENARIOS) - 1):
        ctx.execution_phase = ExecutionPhase.RETRYING_AFTER_CODE_CHANGE
        ctx.test_that_triggered_code_change = (MODULE, FRID)
        render_context.start_conformance_tests_for_frid()

    assert ctx.acceptance_tests_completed == len(SCENARIOS)


def test_regression_starts_when_every_scenario_has_a_test():
    render_context = make_render_context(SCENARIOS, AcceptanceTestPhase.IN_PROGRESS, len(SCENARIOS))

    render_context._on_conformance_test_passed_after_retry()

    ctx = render_context.conformance_tests_running_context
    assert ctx.execution_phase == ExecutionPhase.RUNNING_REGRESSION
    assert ctx.code_changed_during_regression is True


@pytest.mark.parametrize(
    "acceptance_scenarios, acceptance_test_phase, acceptance_tests_completed",
    [
        ([], AcceptanceTestPhase.NOT_APPLICABLE, 1),
        (SCENARIOS, AcceptanceTestPhase.COMPLETED, len(SCENARIOS)),
    ],
    ids=["no acceptance scenario", "acceptance phase completed"],
)
def test_regression_starts_when_the_functionality_has_no_scenario_left(
    acceptance_scenarios, acceptance_test_phase, acceptance_tests_completed
):
    """A code fix during regression keeps the behaviour it had before."""
    render_context = make_render_context(acceptance_scenarios, acceptance_test_phase, acceptance_tests_completed)

    render_context._on_conformance_test_passed_after_retry()

    ctx = render_context.conformance_tests_running_context
    assert ctx.execution_phase == ExecutionPhase.RUNNING_REGRESSION
    assert ctx.current_testing_frid is None
