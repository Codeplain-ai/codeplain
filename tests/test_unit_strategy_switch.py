"""Tests for giving up on patching when the unit fix loop stops making progress.

The unit loop has the same problem as the conformance one and a different remedy: its
escape hatch restarts the functionality from scratch rather than discarding a test file.
That is destructive enough to be worth a careful threshold, and the benchmark data makes
the call easy — across three renders every healthy functionality finished with
`unit_max_repeat=1`, nothing was ever observed between that and the 17 reached by the one
that wedged. So a streak of three is a state healthy renders do not enter, and reaching
the same decision on it saves the eleven minutes that render spent grinding to the
attempt limit.
"""

from unittest.mock import MagicMock, patch

import render_machine.render_context as render_context_module
from render_machine.fix_loop_metrics import (
    CONFORMANCE_LOOP,
    CONSECUTIVE_FAILURE_THRESHOLD,
    STRATEGY_SWITCH_PREFIX,
    UNIT_LOOP,
    FixLoopMetrics,
)
from render_machine.render_context import MAX_UNITTEST_FIX_ATTEMPTS, RenderContext

MODULE = "vault_cli"
FRID = "2"


def context(identical_failures=0, attempts=1, output="AssertionError: vault not initialized"):
    instance = MagicMock(spec=RenderContext)
    instance.module_name = MODULE
    instance.fix_loop_metrics = FixLoopMetrics()
    instance.frid_context = MagicMock()
    instance.frid_context.frid = FRID
    instance.unit_tests_running_context = MagicMock()
    instance.unit_tests_running_context.fix_attempts = attempts
    for _ in range(identical_failures):
        instance.fix_loop_metrics.record(UNIT_LOOP, module=MODULE, frid=FRID, passed=False, output=output)
    return instance


def gave_up(instance):
    """Whether start_fixing_unit_tests reached the give-up handler."""
    on_limit_exceeded = MagicMock()
    with patch.object(render_context_module, "console"):
        RenderContext.start_fixing_unit_tests(instance, on_limit_exceeded)
    return on_limit_exceeded.called


def test_a_unit_loop_repeating_a_failure_three_times_gives_up_early():
    assert gave_up(context(identical_failures=3)) is True


def test_two_repeats_are_left_alone():
    """No healthy functionality in the benchmark data ever reached two, so this is
    already past normal — but the remedy discards the whole functionality, so it waits
    for the same evidence the conformance side does."""
    assert gave_up(context(identical_failures=2)) is False


def test_a_healthy_loop_is_left_alone():
    assert gave_up(context(identical_failures=0)) is False


def test_a_unit_loop_that_always_fails_gives_up_without_a_repeat():
    instance = context()
    for index in range(CONSECUTIVE_FAILURE_THRESHOLD):
        instance.fix_loop_metrics.record(
            UNIT_LOOP, module=MODULE, frid=FRID, passed=False, output=f"failure number {index}"
        )

    assert gave_up(instance) is True


def test_a_stuck_conformance_loop_does_not_restart_the_functionality():
    """The conformance loop has its own, far cheaper remedy; it must not reach this one."""
    instance = context()
    for _ in range(8):
        instance.fix_loop_metrics.record(CONFORMANCE_LOOP, module=MODULE, frid=FRID, passed=False, output="same")

    assert gave_up(instance) is False


def test_the_attempt_limit_still_ends_the_loop_on_its_own():
    """The streak arm is an early exit, not a replacement: a loop that never repeats and
    never accumulates enough consecutive failures still stops at the limit."""
    assert gave_up(context(attempts=MAX_UNITTEST_FIX_ATTEMPTS)) is True


def test_the_early_exit_is_announced_in_a_greppable_form():
    instance = context(identical_failures=4)

    with patch.object(render_context_module, "console") as console:
        RenderContext.start_fixing_unit_tests(instance, MagicMock())

    announced = console.warning.call_args[0][0]
    assert STRATEGY_SWITCH_PREFIX in announced
    assert f"module={MODULE}" in announced
    assert f"frid={FRID}" in announced
    assert "loop=unit" in announced
    assert "repeated_failure streak=4" in announced
    assert "action=give_up_on_patching" in announced


def test_hitting_the_attempt_limit_is_not_announced_as_a_switch():
    """The limit path is ordinary exhaustion, not a decision the loop made about its own
    progress; labelling it a strategy switch would inflate every benchmark count."""
    instance = context(attempts=MAX_UNITTEST_FIX_ATTEMPTS)

    with patch.object(render_context_module, "console") as console:
        RenderContext.start_fixing_unit_tests(instance, MagicMock())

    assert not console.warning.called
