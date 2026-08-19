"""Tests for switching strategy when the conformance fix loop stops making progress.

The loop's failure mode is not slowness, it is repetition: it re-sends the same fix
request, gets back a patch that changes nothing the test can see, and does it again. A
wedged cli-password-manager render failed conformance 20 times on one functionality with
a streak of 8 while its unit loop never failed once, spent 5h20m, and still scored 1/16.
The same signature appears on bookshelf-api, so it is not one spec's quirk.

Regenerating the conformance test is the different move — it discards the test the loop
cannot satisfy instead of editing code against it again. These tests pin when that
happens, and just as importantly when it does not: the threshold has to sit above what a
healthy functionality does, or every good render pays for it.
"""

from unittest.mock import MagicMock, patch

from render_machine.actions.fix_conformance_test import (
    MAX_CONFORMANCE_TEST_RERENDER_ATTEMPTS,
    STRATEGY_SWITCH_PREFIX,
    FixConformanceTest,
)
from render_machine.fix_loop_metrics import CONFORMANCE_LOOP, UNIT_LOOP, FixLoopMetrics

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
    return context


def decides_to_regenerate(context):
    with patch("render_machine.actions.fix_conformance_test.console"):
        return FixConformanceTest._should_regenerate_instead_of_patching(context)


def test_a_loop_that_repeats_a_failure_three_times_regenerates_the_test():
    assert decides_to_regenerate(render_context(identical_failures=3)) is True


def test_a_healthy_functionality_that_repeats_twice_is_left_alone():
    """Observed in a real render: a functionality repeated a failure twice and then
    converged. A threshold of two would abandon tests that were about to pass."""
    assert decides_to_regenerate(render_context(identical_failures=2)) is False


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
    context = render_context(identical_failures=3)
    context.conformance_tests_running_context.current_testing_frid = None

    assert decides_to_regenerate(context) is False


def test_a_stuck_unit_loop_does_not_regenerate_conformance_tests():
    """The two loops fail for different reasons and warrant different responses."""
    context = render_context(identical_failures=0)
    for _ in range(5):
        context.fix_loop_metrics.record(UNIT_LOOP, module=MODULE, frid=FRID, passed=False, output="same")

    assert decides_to_regenerate(context) is False


def test_the_switch_is_spent_once_and_cannot_cycle():
    """Regeneration draws on the same budget as the attempt-limit path. Once it is used
    the loop patches to the limit and stops, rather than regenerating forever."""
    spent = render_context(identical_failures=8, render_attempts=MAX_CONFORMANCE_TEST_RERENDER_ATTEMPTS)

    assert decides_to_regenerate(spent) is False


def test_the_switch_is_announced_in_a_greppable_form():
    """Benchmark runs are read by tooling before they are read by a person."""
    context = render_context(identical_failures=4)

    with patch("render_machine.actions.fix_conformance_test.console") as console:
        FixConformanceTest._should_regenerate_instead_of_patching(context)

    announced = console.warning.call_args[0][0]
    assert STRATEGY_SWITCH_PREFIX in announced
    assert f"module={MODULE}" in announced
    assert f"frid={FRID}" in announced
    assert "conformance_streak=4" in announced
    assert "action=regenerate_conformance_tests" in announced


def test_the_action_returns_the_regeneration_outcome_and_marks_the_context():
    """The early return has to reach the state machine the same way the attempt-limit
    path does, or the render carries on patching regardless of the decision."""
    context = render_context(identical_failures=3)

    with patch("render_machine.actions.fix_conformance_test.console"):
        outcome, payload = FixConformanceTest().execute(context, {"previous_conformance_tests_issue": "boom"})

    assert outcome == FixConformanceTest.REGENERATE_CONFORMANCE_TESTS_OUTCOME
    assert payload is None
    assert context.conformance_tests_running_context.regenerating_conformance_tests is True


def test_the_api_is_not_asked_for_another_patch_when_switching():
    """The point of the switch is to stop spending fix requests on a test the loop
    cannot satisfy."""
    context = render_context(identical_failures=3)

    with patch("render_machine.actions.fix_conformance_test.console"):
        FixConformanceTest().execute(context, {"previous_conformance_tests_issue": "boom"})

    context.codeplain_api.fix_conformance_tests_issue.assert_not_called()
