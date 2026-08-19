"""Tests for re-delivering end-of-file to a target that has gone quiet.

Every execution gets one end-of-file at spawn. `getpass` calls
`tcsetattr(..., TCSAFLUSH, ...)` before reading, and TCSAFLUSH discards pending input —
so the EOF is gone by the time the read happens and the program waits for input nobody
will send. The script loses its whole timeout, and the unit-test fix loop reads that as a
defect in the code: one benchmark render patched against the resulting failure seventeen
times in a row while its conformance loop never failed once.

Nothing else answers a test script's terminal reads, so the wait has to answer for
itself.
"""

import time
from unittest.mock import MagicMock

import pytest

from render_machine.render_utils import (
    EOF_BYTE,
    MAX_EOF_RESENDS,
    QUIET_BEFORE_EOF_RESEND_SECONDS,
    _QuietEofResender,
)


@pytest.fixture
def target():
    process = MagicMock()
    process.transcript = ""
    process.normalized_output = lambda: process.transcript
    return process


def quiet_for(resender, seconds):
    """Runs a poll as though `seconds` of silence had passed."""
    resender._since -= seconds
    resender.consider()


def test_a_quiet_target_is_sent_end_of_file_again(target):
    resender = _QuietEofResender(target)
    resender.consider()  # establishes the baseline

    quiet_for(resender, QUIET_BEFORE_EOF_RESEND_SECONDS)

    target.write_input.assert_called_once_with(EOF_BYTE)


def test_a_target_still_producing_output_is_left_alone(target):
    """Output means the program is working, not waiting."""
    resender = _QuietEofResender(target)
    resender.consider()

    target.transcript = "still going"
    quiet_for(resender, QUIET_BEFORE_EOF_RESEND_SECONDS)

    target.write_input.assert_not_called()


def test_a_briefly_quiet_target_is_left_alone(target):
    resender = _QuietEofResender(target)
    resender.consider()

    quiet_for(resender, QUIET_BEFORE_EOF_RESEND_SECONDS / 2)

    target.write_input.assert_not_called()


def test_output_after_a_resend_restarts_the_clock(target):
    """A program that answers the end-of-file and carries on is making progress."""
    resender = _QuietEofResender(target)
    resender.consider()
    quiet_for(resender, QUIET_BEFORE_EOF_RESEND_SECONDS)
    target.write_input.reset_mock()

    target.transcript = "off it goes"
    resender.consider()
    quiet_for(resender, QUIET_BEFORE_EOF_RESEND_SECONDS / 2)

    target.write_input.assert_not_called()


def test_the_resends_are_bounded(target):
    """A target quiet through every delivery is not waiting on the terminal, and a stuck
    script should not also be a noisy one."""
    resender = _QuietEofResender(target)
    resender.consider()

    for _ in range(MAX_EOF_RESENDS + 5):
        quiet_for(resender, QUIET_BEFORE_EOF_RESEND_SECONDS)

    assert target.write_input.call_count == MAX_EOF_RESENDS


def test_a_target_that_cannot_be_written_to_stops_being_tried(target):
    """The wait loop owns what happens to an unwritable target; this must not turn one
    broken write into a warning on every poll."""
    target.write_input.side_effect = OSError("the terminal is gone")
    resender = _QuietEofResender(target)
    resender.consider()

    for _ in range(3):
        quiet_for(resender, QUIET_BEFORE_EOF_RESEND_SECONDS)

    assert target.write_input.call_count == 1


def test_the_first_poll_does_not_immediately_resend(target):
    """A target gets its quiet period before anything is concluded about it."""
    resender = _QuietEofResender(target)

    resender.consider()

    target.write_input.assert_not_called()


def test_a_real_clock_is_used_for_the_quiet_period(target):
    """Guards the constant itself: a period short enough to fire between two polls would
    write into every healthy script that pauses to think."""
    assert QUIET_BEFORE_EOF_RESEND_SECONDS >= 1.0

    resender = _QuietEofResender(target)
    resender.consider()
    time.sleep(0.05)
    resender.consider()

    target.write_input.assert_not_called()
