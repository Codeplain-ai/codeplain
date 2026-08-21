"""Tests for the fix-loop instrumentation.

Every render that produced no code was a fix loop spending its whole budget
re-patching one file against one failure. The loop could not tell it was stuck, and the
only externally visible outcome was a rare binary "did the render abort" — too coarse to
compare configurations against. This turns both into observations: a streak counter that
names a repeated-identical failure while it is happening, and per-FRID attempt counts
that make convergence a continuous measure.

The fingerprint has to survive the parts of a test-script's output that change on every
run — temp paths, durations, addresses — while still separating genuinely different
failures, since both mistakes destroy the signal in opposite directions.
"""

import pytest

from render_machine.fix_loop_metrics import (
    CONFORMANCE_LOOP,
    CONSECUTIVE_FAILURE_THRESHOLD,
    UNIT_LOOP,
    FixLoopMetrics,
    failure_fingerprint,
    stalled_reason,
)


def test_the_same_failure_fingerprints_the_same():
    first = "FAILED test_header.py::test_subtitle\nAssertionError: subtitle not shown"
    second = "FAILED test_header.py::test_subtitle\nAssertionError: subtitle not shown"

    assert failure_fingerprint(first) == failure_fingerprint(second)


def test_volatile_noise_does_not_change_the_fingerprint():
    """Two runs of one failing suite differ in temp path, duration and address."""
    first = (
        "Output stored in /tmp/tmpk8flk7f1.script_output\n# duration_ms 1335.821531\nat 0x7f3a2b1c AssertionError: x"
    )
    second = "Output stored in /tmp/tmpy0wo02yi.script_output\n# duration_ms 22.5\nat 0x55e1ff90 AssertionError: x"

    assert failure_fingerprint(first) == failure_fingerprint(second)


def test_a_different_failure_fingerprints_differently():
    assert failure_fingerprint("AssertionError: subtitle not shown") != failure_fingerprint(
        "AssertionError: button not found"
    )


def test_a_first_failure_is_not_a_repeat():
    metrics = FixLoopMetrics()

    streak = metrics.record(UNIT_LOOP, module="m", frid="1", passed=False, output="boom")

    assert streak is None


def test_the_same_failure_twice_reports_a_streak():
    metrics = FixLoopMetrics()

    metrics.record(UNIT_LOOP, module="m", frid="1", passed=False, output="boom")
    streak = metrics.record(UNIT_LOOP, module="m", frid="1", passed=False, output="boom")

    assert streak == 2


def test_a_different_failure_restarts_the_streak():
    metrics = FixLoopMetrics()

    metrics.record(UNIT_LOOP, module="m", frid="1", passed=False, output="boom")
    metrics.record(UNIT_LOOP, module="m", frid="1", passed=False, output="boom")
    streak = metrics.record(UNIT_LOOP, module="m", frid="1", passed=False, output="different")

    assert streak is None


def test_the_two_loops_are_counted_apart():
    """A unit-test failure must not extend a conformance streak, or vice versa."""
    metrics = FixLoopMetrics()

    metrics.record(UNIT_LOOP, module="m", frid="1", passed=False, output="boom")
    streak = metrics.record(CONFORMANCE_LOOP, module="m", frid="1", passed=False, output="boom")

    assert streak is None


def test_each_frid_counts_its_own_attempts():
    metrics = FixLoopMetrics()

    metrics.record(UNIT_LOOP, module="m", frid="1", passed=False, output="a")
    metrics.record(UNIT_LOOP, module="m", frid="1", passed=True, output="")
    metrics.record(UNIT_LOOP, module="m", frid="2", passed=True, output="")

    assert (
        metrics.frid_summary("m", "1")
        == "[fix-loop] module=m frid=1 unit=2 unit_failed=1 unit_max_repeat=1 max_repeat=1"
    )
    assert (
        metrics.frid_summary("m", "2")
        == "[fix-loop] module=m frid=2 unit=1 unit_failed=0 unit_max_repeat=1 max_repeat=1"
    )


def test_a_frid_summary_reports_both_loops_and_the_worst_streak():
    metrics = FixLoopMetrics()

    for _ in range(3):
        metrics.record(CONFORMANCE_LOOP, module="m", frid="2", passed=False, output="same")
    metrics.record(UNIT_LOOP, module="m", frid="2", passed=True, output="")

    summary = metrics.frid_summary("m", "2")

    assert "conformance=3" in summary
    assert "conformance_failed=3" in summary
    assert "unit=1" in summary
    assert "max_repeat=3" in summary


def test_each_loop_reports_its_own_streak():
    """The aggregate cannot say which loop wedged, and the two wedge for different
    reasons: a stuck unit loop means the implementation is not moving, a stuck
    conformance loop can mean the test script never even ran. Reading a run where the
    unit loop repeated seven times and conformance only three, the single number says
    7 and invites the reader to attribute it to conformance."""
    metrics = FixLoopMetrics()
    for _ in range(7):
        metrics.record(UNIT_LOOP, module="m", frid="3", passed=False, output="same unit failure")
    for _ in range(3):
        metrics.record(CONFORMANCE_LOOP, module="m", frid="3", passed=False, output="same conformance failure")

    summary = metrics.frid_summary("m", "3")

    assert "unit_max_repeat=7" in summary
    assert "conformance_max_repeat=3" in summary
    assert "max_repeat=7" in summary  # the aggregate stays, for continuity of the series


def test_the_current_streak_is_readable_after_the_fact():
    """The fix action runs after the test action and has to ask again, from its own call
    site, rather than relying on what record() returned to someone else."""
    metrics = FixLoopMetrics()
    for _ in range(3):
        metrics.record(CONFORMANCE_LOOP, module="m", frid="1", passed=False, output="same")

    assert metrics.current_streak(CONFORMANCE_LOOP, "m", "1") == 3


def test_the_current_streak_resets_when_the_failure_changes():
    metrics = FixLoopMetrics()
    for _ in range(3):
        metrics.record(CONFORMANCE_LOOP, module="m", frid="1", passed=False, output="same")
    metrics.record(CONFORMANCE_LOOP, module="m", frid="1", passed=False, output="different")

    assert metrics.current_streak(CONFORMANCE_LOOP, "m", "1") == 1


def test_the_current_streak_clears_when_the_loop_passes():
    metrics = FixLoopMetrics()
    for _ in range(3):
        metrics.record(CONFORMANCE_LOOP, module="m", frid="1", passed=False, output="same")
    metrics.record(CONFORMANCE_LOOP, module="m", frid="1", passed=True, output="")

    assert metrics.current_streak(CONFORMANCE_LOOP, "m", "1") == 0


def test_an_unrun_loop_has_no_streak():
    metrics = FixLoopMetrics()
    metrics.record(UNIT_LOOP, module="m", frid="1", passed=False, output="boom")

    assert metrics.current_streak(CONFORMANCE_LOOP, "m", "1") == 0
    assert metrics.current_streak(CONFORMANCE_LOOP, "m", "9") == 0


def test_an_unseen_frid_has_no_summary():
    assert FixLoopMetrics().frid_summary("m", "9") is None


def test_the_render_summary_covers_every_frid_touched():
    metrics = FixLoopMetrics()
    metrics.record(UNIT_LOOP, module="m", frid="1", passed=True, output="")
    metrics.record(CONFORMANCE_LOOP, module="m", frid="2", passed=False, output="x")

    lines = metrics.render_summary()

    assert len(lines) == 2
    assert any("frid=1" in line for line in lines)
    assert any("frid=2" in line for line in lines)


def test_the_render_summary_is_empty_when_no_script_ran():
    """A render that failed before any test script must not emit a misleading summary."""
    assert FixLoopMetrics().render_summary() == []


@pytest.mark.parametrize(
    "first,second",
    [
        ("/tmp/tmpk8flk7f1.script_output", "/tmp/tmpQQQQQQQQ.script_output"),
        (
            "/var/folders/qk/zjb6qvds0pl4_g9vqjv3cjt80000gn/T/tmpk8flk7f1",
            "/var/folders/p2/x9wc0mn11rs7_h1abcd2efg30000hn/T/tmpQQQQQQQQ",
        ),
        ("/private/var/folders/qk/zjb6qvds0pl4/T/tmpk8flk7f1", "/private/var/folders/p2/x9wc0mn11rs/T/tmpQQQQQQQQ"),
        (r"C:\Users\runner\AppData\Local\Temp\tmpk8flk7f1", r"C:\Users\runner\AppData\Local\Temp\tmpQQQQQQQQ"),
    ],
    ids=["linux", "macos", "macos-private", "windows"],
)
def test_a_scratch_path_never_makes_one_failure_look_like_two(first, second):
    """Two identical failures must fingerprint the same on every platform, or a repeat is
    never recognised and the loop never looks stalled."""
    assert failure_fingerprint(f"AssertionError at {first} line 3") == failure_fingerprint(
        f"AssertionError at {second} line 3"
    )


def test_a_frid_reports_its_summary_only_once():
    """Otherwise the end-of-render sweep counts every completed FRID a second time."""
    metrics = FixLoopMetrics()
    metrics.record(CONFORMANCE_LOOP, module="m", frid="1", passed=False, output="boom")

    assert metrics.take_frid_summary("m", "1") is not None
    assert metrics.take_frid_summary("m", "1") is None
    assert metrics.render_summary() == []


def test_a_regenerated_test_is_not_condemned_by_the_old_test_s_stall():
    """Regeneration hands the loop a different test. If the stall measured against the
    deleted one survives, the replacement is discarded on its first failure."""
    metrics = FixLoopMetrics()
    for _ in range(CONSECUTIVE_FAILURE_THRESHOLD):
        metrics.record(CONFORMANCE_LOOP, module="m", frid="2", passed=False, output="always different %s")

    assert stalled_reason(metrics, CONFORMANCE_LOOP, module="m", frid="2") is not None

    metrics.start_over(CONFORMANCE_LOOP, module="m", frid="2")

    assert stalled_reason(metrics, CONFORMANCE_LOOP, module="m", frid="2") is None

    # One failure of the replacement must not re-trigger the switch on its own.
    metrics.record(CONFORMANCE_LOOP, module="m", frid="2", passed=False, output="a new failure")

    assert stalled_reason(metrics, CONFORMANCE_LOOP, module="m", frid="2") is None


def test_starting_over_keeps_the_work_already_counted():
    """The cumulative counts answer a different question, so a reset must not erase them."""
    metrics = FixLoopMetrics()
    for _ in range(4):
        metrics.record(CONFORMANCE_LOOP, module="m", frid="2", passed=False, output="identical")

    metrics.start_over(CONFORMANCE_LOOP, module="m", frid="2")
    summary = metrics.frid_summary("m", "2")

    assert "conformance=4" in summary
    assert "conformance_failed=4" in summary
    assert "conformance_max_repeat=4" in summary
