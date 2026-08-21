"""Tests for switching strategy when the conformance fix loop stops making progress.

The loop's failure mode is not slowness, and it comes in two shapes. It can re-send the
same fix request and get back a patch that changes nothing the test can see — a wedged
render failed conformance 20 times on one functionality with a streak of 8 while its unit
loop never failed once. Or it can fail every single time while the failures keep changing
shape — another went 40 for 40 with a longest identical run of two, which no streak
threshold can catch. Both burn the whole budget.

Regenerating the conformance test is the different move — it discards the test the loop
cannot satisfy instead of editing code against it again. These tests pin when that
happens, and just as importantly when it does not: both thresholds have to sit above what
a healthy functionality does, or every good render pays for it.
"""

from unittest.mock import MagicMock, patch

from render_machine.actions.fix_conformance_test import MAX_CONFORMANCE_TEST_RERENDER_ATTEMPTS, FixConformanceTest
from render_machine.fix_loop_metrics import (
    CONFORMANCE_LOOP,
    CONSECUTIVE_FAILURE_THRESHOLD,
    REPEATED_FAILURE_WARNING_THRESHOLD,
    STRATEGY_SWITCH_PREFIX,
    UNIT_LOOP,
    FixLoopMetrics,
)
from render_machine.render_types import AcceptanceTestPhase

MODULE = "vault_cli"
FRID = "2"


def render_context(identical_failures=0, render_attempts=0, output="AssertionError: prompt not shown"):
    context = MagicMock()
    context.module_name = MODULE
    context.fix_loop_metrics = FixLoopMetrics()
    for _ in range(identical_failures):
        context.fix_loop_metrics.record(CONFORMANCE_LOOP, module=MODULE, frid=FRID, passed=False, output=output)

    ctx = context.conformance_tests_running_context
    ctx.current_testing_frid = FRID
    ctx.current_testing_module_name = MODULE
    ctx.conformance_tests_render_attempts = render_attempts
    ctx.fix_attempts = 4  # mid-loop: well below the attempt limit
    ctx.regenerating_conformance_tests = False
    ctx.acceptance_test_phase = AcceptanceTestPhase.NOT_STARTED
    return context


def decides_to_regenerate(context):
    with patch("render_machine.actions.fix_conformance_test.console"):
        return FixConformanceTest._should_regenerate_instead_of_patching(context)


def test_a_loop_that_repeats_a_failure_three_times_regenerates_the_test():
    assert decides_to_regenerate(render_context(identical_failures=REPEATED_FAILURE_WARNING_THRESHOLD)) is True


def test_a_healthy_functionality_that_repeats_twice_is_left_alone():
    """Observed in a real render: a functionality repeated a failure twice and then
    converged. A threshold of two would abandon tests that were about to pass."""
    assert REPEATED_FAILURE_WARNING_THRESHOLD >= 2, "one below the threshold has to be a real repeat"
    assert decides_to_regenerate(render_context(identical_failures=REPEATED_FAILURE_WARNING_THRESHOLD - 1)) is False


def test_a_first_failure_does_not_trigger_it():
    assert decides_to_regenerate(render_context(identical_failures=1)) is False


def test_a_loop_that_has_not_run_yet_does_not_trigger_it():
    assert decides_to_regenerate(render_context(identical_failures=0)) is False


def test_failures_that_differ_do_not_count_as_repeats():
    """A loop making progress produces new failures; only identical ones prove it is
    stuck."""
    context = render_context(identical_failures=0)
    for output in ("first failure", "second failure", "third failure"):
        context.fix_loop_metrics.record(CONFORMANCE_LOOP, module=MODULE, frid=FRID, passed=False, output=output)

    assert decides_to_regenerate(context) is False


def test_a_render_with_no_functionality_under_test_does_not_trigger_it():
    """current_testing_frid is optional; nothing is recorded under a missing one."""
    context = render_context(identical_failures=REPEATED_FAILURE_WARNING_THRESHOLD)
    context.conformance_tests_running_context.current_testing_frid = None

    assert decides_to_regenerate(context) is False


def test_a_stuck_unit_loop_does_not_regenerate_conformance_tests():
    """The two loops fail for different reasons and warrant different responses."""
    context = render_context(identical_failures=0)
    for _ in range(5):
        context.fix_loop_metrics.record(UNIT_LOOP, module=MODULE, frid=FRID, passed=False, output="same")

    assert decides_to_regenerate(context) is False


def test_the_switch_stops_once_its_budget_is_spent_and_cannot_cycle():
    """Regeneration draws on the same budget as the attempt-limit path. Once it is spent
    the loop patches to the limit and stops, rather than regenerating forever."""
    spent = render_context(
        identical_failures=REPEATED_FAILURE_WARNING_THRESHOLD + 1,
        render_attempts=MAX_CONFORMANCE_TEST_RERENDER_ATTEMPTS,
    )

    assert decides_to_regenerate(spent) is False


def test_a_functionality_may_be_regenerated_more_than_once():
    """The budget that mattered. Every render that produced no code died at the attempt
    limit, reachable only after this budget ran out — one regeneration, then twenty
    fruitless patches. A render that completed needed one regeneration on each of three
    functionalities; the ones that wedged needed a second on a single functionality and had
    none. Stopping after the first abandons the render where the move is still working."""
    # Deliberately not `range(1, MAX_CONFORMANCE_TEST_RERENDER_ATTEMPTS)`: a bound derived
    # from the constant makes the test vacuous at the value it is meant to rule out, and it
    # passed against a budget of 1 for exactly that reason. One is the count that has to be
    # named literally here, because one is what the wedged renders got.
    already_regenerated_once = render_context(
        identical_failures=REPEATED_FAILURE_WARNING_THRESHOLD + 1, render_attempts=1
    )

    assert decides_to_regenerate(already_regenerated_once) is True

    assert MAX_CONFORMANCE_TEST_RERENDER_ATTEMPTS >= 2, (
        "the budget has to allow a second regeneration for the assertion above to mean "
        "anything; at 1 the loop abandons a functionality the move was still working on"
    )


def test_the_switch_is_announced_in_a_greppable_form():
    """These logs are read by tooling before they are read by a person."""
    context = render_context(identical_failures=REPEATED_FAILURE_WARNING_THRESHOLD + 1)

    with patch("render_machine.actions.fix_conformance_test.console") as console:
        FixConformanceTest._should_regenerate_instead_of_patching(context)

    announced = console.warning.call_args[0][0]
    assert STRATEGY_SWITCH_PREFIX in announced
    assert f"module={MODULE}" in announced
    assert f"frid={FRID}" in announced
    assert "loop=conformance" in announced
    assert f"repeated_failure streak={REPEATED_FAILURE_WARNING_THRESHOLD + 1}" in announced
    assert "action=regenerate_conformance_tests" in announced


def failing_differently(context, times):
    for index in range(times):
        context.fix_loop_metrics.record(
            CONFORMANCE_LOOP, module=MODULE, frid=FRID, passed=False, output=f"failure number {index}"
        )
    return context


def test_a_loop_that_always_fails_regenerates_even_without_a_repeat():
    """The case a streak trigger cannot see at any threshold: a render failed a
    functionality's conformance tests 40 times out of 40 while its longest
    identical run was two, exhausted its whole budget, and the switch stayed silent."""
    context = failing_differently(render_context(), CONSECUTIVE_FAILURE_THRESHOLD)

    assert decides_to_regenerate(context) is True


def test_a_loop_short_of_the_consecutive_bound_is_left_alone():
    context = failing_differently(render_context(), CONSECUTIVE_FAILURE_THRESHOLD - 1)

    assert decides_to_regenerate(context) is False


def test_a_pass_clears_the_consecutive_count():
    """A loop that gets a test passing is making progress, however many failures it took
    to get there."""
    context = failing_differently(render_context(), CONSECUTIVE_FAILURE_THRESHOLD)
    context.fix_loop_metrics.record(CONFORMANCE_LOOP, module=MODULE, frid=FRID, passed=True, output="")
    failing_differently(context, 1)

    assert decides_to_regenerate(context) is False


def test_the_consecutive_arm_is_announced_with_its_own_reason():
    """The two arms mean different things to a reader, so the marker distinguishes
    them rather than reporting one cause for both."""
    context = failing_differently(render_context(), CONSECUTIVE_FAILURE_THRESHOLD)

    with patch("render_machine.actions.fix_conformance_test.console") as console:
        FixConformanceTest._should_regenerate_instead_of_patching(context)

    announced = console.warning.call_args[0][0]
    assert f"no_progress consecutive_failures={CONSECUTIVE_FAILURE_THRESHOLD}" in announced


def test_the_action_returns_the_regeneration_outcome_and_marks_the_context():
    """The early return has to reach the state machine the same way the attempt-limit
    path does, or the render carries on patching regardless of the decision."""
    context = render_context(identical_failures=REPEATED_FAILURE_WARNING_THRESHOLD)

    with patch("render_machine.actions.fix_conformance_test.console"):
        outcome, payload = FixConformanceTest().execute(context, {"previous_conformance_tests_issue": "boom"})

    assert outcome == FixConformanceTest.REGENERATE_CONFORMANCE_TESTS_OUTCOME
    assert payload is None
    assert context.conformance_tests_running_context.regenerating_conformance_tests is True


def test_the_api_is_not_asked_for_another_patch_when_switching():
    """The point of the switch is to stop spending fix requests on a test the loop
    cannot satisfy."""
    context = render_context(identical_failures=REPEATED_FAILURE_WARNING_THRESHOLD)

    with patch("render_machine.actions.fix_conformance_test.console"):
        FixConformanceTest().execute(context, {"previous_conformance_tests_issue": "boom"})

    context.codeplain_api.fix_conformance_tests_issue.assert_not_called()


def test_a_required_modules_test_is_not_regenerated_by_the_switch():
    """The replacement is rendered under the current module's folder while the required
    module stays under test, so the next lookup rejects the prefix."""
    context = render_context(identical_failures=REPEATED_FAILURE_WARNING_THRESHOLD + 1)
    context.conformance_tests_running_context.current_testing_module_name = "a_required_module"

    assert decides_to_regenerate(context) is False


def test_an_acceptance_phase_in_flight_is_not_regenerated_by_the_switch():
    """Regeneration removes the entry and its folder; the incremental phase then looks up
    an entry that is gone."""
    context = render_context(identical_failures=REPEATED_FAILURE_WARNING_THRESHOLD + 1)
    context.conformance_tests_running_context.acceptance_test_phase = AcceptanceTestPhase.IN_PROGRESS

    assert decides_to_regenerate(context) is False
