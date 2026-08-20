"""Tests for where the fix-loop instrumentation is wired in.

Counting is only useful if every script run reaches the counter and the counts survive
the paths a render actually takes — including the failing one, where the FRID that
exhausted its budget never reaches FinishFunctionalRequirement and would otherwise take
its numbers with it.
"""

from unittest.mock import MagicMock, patch

from render_machine.actions.exit_with_error import ExitWithError
from render_machine.fix_loop_metrics import (
    CONFORMANCE_LOOP,
    REPEATED_FAILURE_WARNING_THRESHOLD,
    UNIT_LOOP,
    FixLoopMetrics,
    report_fix_loop_attempt,
)


def render_context():
    context = MagicMock()
    context.module_name = "m"
    context.fix_loop_metrics = FixLoopMetrics()
    context.last_error_message = "stopped"
    context.frid_context.frid = "1"
    return context


def test_an_attempt_without_a_frid_is_not_counted():
    """Test scripts also run outside a functionality (module setup); those attempts
    belong to no FRID and must not be attributed to one."""
    context = render_context()

    report_fix_loop_attempt(context, loop=UNIT_LOOP, frid=None, passed=False, output="boom")

    assert context.fix_loop_metrics.render_summary() == []


def test_a_repeated_failure_warns_only_once_it_is_clearly_stuck():
    context = render_context()

    with patch("render_machine.fix_loop_metrics.console") as console:
        for _ in range(REPEATED_FAILURE_WARNING_THRESHOLD - 1):
            report_fix_loop_attempt(context, loop=CONFORMANCE_LOOP, frid="2", passed=False, output="same")

        assert console.warning.call_count == 0, "warned before the loop was demonstrably stuck"

        report_fix_loop_attempt(context, loop=CONFORMANCE_LOOP, frid="2", passed=False, output="same")

    assert console.warning.call_count == 1
    warning = console.warning.call_args[0][0]
    assert "functionality 2" in warning
    assert f"{REPEATED_FAILURE_WARNING_THRESHOLD} times in a row" in warning


def test_progress_keeps_the_loop_quiet():
    context = render_context()

    with patch("render_machine.fix_loop_metrics.console") as console:
        for attempt in range(6):
            report_fix_loop_attempt(context, loop=UNIT_LOOP, frid="1", passed=False, output=f"failure {attempt}")

    assert console.warning.call_count == 0


def test_a_failed_render_still_reports_its_counts():
    """The exhausted FRID never reaches FinishFunctionalRequirement."""
    context = render_context()
    report_fix_loop_attempt(context, loop=CONFORMANCE_LOOP, frid="2", passed=False, output="x")

    with patch("render_machine.actions.exit_with_error.console") as console:
        ExitWithError().execute(context, None)

    reported = [call[0][0] for call in console.info.call_args_list]
    assert any("[fix-loop]" in line and "frid=2" in line for line in reported)
