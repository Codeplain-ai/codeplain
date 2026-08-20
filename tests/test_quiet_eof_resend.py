"""Tests for re-delivering end-of-file to a target that has gone quiet.

Every execution gets one end-of-file at spawn. `getpass` calls
`tcsetattr(..., TCSAFLUSH, ...)` before reading, and TCSAFLUSH discards pending input —
so the EOF is gone by the time the read happens and the program waits for input nobody
will send. The script loses its whole timeout, and the unit-test fix loop reads that as a
defect in the code: one render patched against the resulting failure seventeen times in
a row while its conformance loop never failed once.

Nothing else answers a test script's terminal reads, so the wait has to answer for
itself. Two things bound that: quiet is measured in bytes read rather than in the size of
the rendered screen, and the delivery belongs to the backend that owns the terminal.
"""

import time
from unittest.mock import MagicMock

import pytest

from render_machine.render_utils import (
    MAX_EOF_RESENDS,
    QUIET_BEFORE_EOF_RESEND_SECONDS,
    _QuietEofResender,
)


@pytest.fixture
def target():
    process = MagicMock()
    process.output_bytes_seen = 0
    process.redeliver_end_of_file = MagicMock(return_value=True)
    return process


def quiet_for(resender, seconds):
    """Runs a poll as though `seconds` of silence had passed."""
    resender._since -= seconds
    resender.consider()


def test_a_quiet_target_is_sent_end_of_file_again(target):
    resender = _QuietEofResender(target)
    resender.consider()  # establishes the baseline

    quiet_for(resender, QUIET_BEFORE_EOF_RESEND_SECONDS)

    target.redeliver_end_of_file.assert_called_once_with()


def test_a_target_still_producing_output_is_left_alone(target):
    """Output means the program is working, not waiting."""
    resender = _QuietEofResender(target)
    resender.consider()

    target.output_bytes_seen = 11
    quiet_for(resender, QUIET_BEFORE_EOF_RESEND_SECONDS)

    target.redeliver_end_of_file.assert_not_called()


def test_a_repainting_target_is_still_producing_output(target):
    """The rendered transcript is a screen. A spinner, a progress line rewritten in place,
    or a bounded transcript that has filled all leave its length unchanged while bytes keep
    arriving, and each would otherwise be read as silence and answered with an end-of-file
    landing in a read it was never meant for."""
    resender = _QuietEofResender(target)
    target.normalized_output = lambda: "working [####------]"  # same length on every poll
    resender.consider()

    for tick in range(1, 4):
        target.output_bytes_seen = tick * 64  # bytes keep arriving
        quiet_for(resender, QUIET_BEFORE_EOF_RESEND_SECONDS)

    target.redeliver_end_of_file.assert_not_called()


def test_a_backend_that_cannot_deliver_end_of_file_is_asked_once(target):
    """ConPTY has no parent-side end-of-file, and the pipe backend already gave the child
    DEVNULL. Writing a byte at them anyway would be ordinary input, not an end-of-file."""
    target.redeliver_end_of_file.return_value = False
    resender = _QuietEofResender(target)
    resender.consider()

    for _ in range(4):
        quiet_for(resender, QUIET_BEFORE_EOF_RESEND_SECONDS)

    assert target.redeliver_end_of_file.call_count == 1


def test_the_resender_never_writes_input_itself(target):
    """The byte is the backend's to choose: the terminal's VEOF may not be 0x04, and the
    echo has to be suppressed around it or a literal ^D lands in the transcript."""
    resender = _QuietEofResender(target)
    resender.consider()

    quiet_for(resender, QUIET_BEFORE_EOF_RESEND_SECONDS)

    target.write_input.assert_not_called()


def test_a_briefly_quiet_target_is_left_alone(target):
    resender = _QuietEofResender(target)
    resender.consider()

    quiet_for(resender, QUIET_BEFORE_EOF_RESEND_SECONDS / 2)

    target.redeliver_end_of_file.assert_not_called()


def test_output_after_a_resend_restarts_the_clock(target):
    """A program that answers the end-of-file and carries on is making progress."""
    resender = _QuietEofResender(target)
    resender.consider()
    quiet_for(resender, QUIET_BEFORE_EOF_RESEND_SECONDS)
    target.redeliver_end_of_file.reset_mock()

    target.output_bytes_seen = 12
    resender.consider()
    quiet_for(resender, QUIET_BEFORE_EOF_RESEND_SECONDS / 2)

    target.redeliver_end_of_file.assert_not_called()


def test_the_resends_are_bounded(target):
    """A target quiet through every delivery is not waiting on the terminal, and a stuck
    script should not also be a noisy one."""
    resender = _QuietEofResender(target)
    resender.consider()

    for _ in range(MAX_EOF_RESENDS + 5):
        quiet_for(resender, QUIET_BEFORE_EOF_RESEND_SECONDS)

    assert target.redeliver_end_of_file.call_count == MAX_EOF_RESENDS


def test_a_target_that_cannot_be_written_to_stops_being_tried(target):
    """The wait loop owns what happens to an unwritable target; this must not turn one
    broken write into a warning on every poll."""
    target.redeliver_end_of_file.side_effect = OSError("the terminal is gone")
    resender = _QuietEofResender(target)
    resender.consider()

    for _ in range(3):
        quiet_for(resender, QUIET_BEFORE_EOF_RESEND_SECONDS)

    assert target.redeliver_end_of_file.call_count == 1


def test_the_first_poll_does_not_immediately_resend(target):
    """A target gets its quiet period before anything is concluded about it."""
    resender = _QuietEofResender(target)

    resender.consider()

    target.redeliver_end_of_file.assert_not_called()


def test_a_real_clock_is_used_for_the_quiet_period(target):
    """Guards the constant itself: a period short enough to fire between two polls would
    write into every healthy script that pauses to think."""
    assert QUIET_BEFORE_EOF_RESEND_SECONDS >= 1.0

    resender = _QuietEofResender(target)
    resender.consider()
    time.sleep(0.05)
    resender.consider()

    target.redeliver_end_of_file.assert_not_called()
