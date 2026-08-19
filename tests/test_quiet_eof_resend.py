"""Tests for re-delivering end-of-file to a driverless target that has gone quiet.

A driverless execution gets one end-of-file at spawn. `getpass` calls
`tcsetattr(..., TCSAFLUSH, ...)` before reading, and TCSAFLUSH discards pending input —
so the EOF is gone by the time the read happens and the program waits for input nobody
will send. The script loses its whole timeout, and the unit-test fix loop reads that as a
defect in the code: one benchmark render patched against the resulting failure seventeen
times in a row while its conformance loop never failed once.

The broker exists for exactly this, but unit tests never get one — they are part of the
delivered codebase and must not depend on Codeplain's test tooling. So the driverless
path has to answer for itself.
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
    resender = _QuietEofResender(target, driverless=True)
    resender.consider()  # establishes the baseline

    quiet_for(resender, QUIET_BEFORE_EOF_RESEND_SECONDS)

    target.write_input.assert_called_once_with(EOF_BYTE)


def test_a_target_still_producing_output_is_left_alone(target):
    """Output means the program is working, not waiting."""
    resender = _QuietEofResender(target, driverless=True)
    resender.consider()

    target.transcript = "still going"
    quiet_for(resender, QUIET_BEFORE_EOF_RESEND_SECONDS)

    target.write_input.assert_not_called()


def test_a_briefly_quiet_target_is_left_alone(target):
    resender = _QuietEofResender(target, driverless=True)
    resender.consider()

    quiet_for(resender, QUIET_BEFORE_EOF_RESEND_SECONDS / 2)

    target.write_input.assert_not_called()


def test_output_after_a_resend_restarts_the_clock(target):
    """A program that answers the end-of-file and carries on is making progress."""
    resender = _QuietEofResender(target, driverless=True)
    resender.consider()
    quiet_for(resender, QUIET_BEFORE_EOF_RESEND_SECONDS)
    target.write_input.reset_mock()

    target.transcript = "off it goes"
    resender.consider()
    quiet_for(resender, QUIET_BEFORE_EOF_RESEND_SECONDS / 2)

    target.write_input.assert_not_called()


def test_a_target_attached_to_a_driver_is_never_written_to(target):
    """A broker-backed run has something driving its terminal deliberately; pushing an
    end-of-file into it would answer a prompt the test meant to answer itself."""
    resender = _QuietEofResender(target, driverless=False)
    resender.consider()

    quiet_for(resender, QUIET_BEFORE_EOF_RESEND_SECONDS * 10)

    target.write_input.assert_not_called()


def test_the_resends_are_bounded(target):
    """A target quiet through every delivery is not waiting on the terminal, and a stuck
    script should not also be a noisy one."""
    resender = _QuietEofResender(target, driverless=True)
    resender.consider()

    for _ in range(MAX_EOF_RESENDS + 5):
        quiet_for(resender, QUIET_BEFORE_EOF_RESEND_SECONDS)

    assert target.write_input.call_count == MAX_EOF_RESENDS


def test_a_target_that_cannot_be_written_to_stops_being_tried(target):
    """The wait loop owns what happens to an unwritable target; this must not turn one
    broken write into a warning on every poll."""
    target.write_input.side_effect = OSError("the terminal is gone")
    resender = _QuietEofResender(target, driverless=True)
    resender.consider()

    for _ in range(3):
        quiet_for(resender, QUIET_BEFORE_EOF_RESEND_SECONDS)

    assert target.write_input.call_count == 1


def test_the_first_poll_does_not_immediately_resend(target):
    """A target gets its quiet period before anything is concluded about it."""
    resender = _QuietEofResender(target, driverless=True)

    resender.consider()

    target.write_input.assert_not_called()


def test_a_real_clock_is_used_for_the_quiet_period(target):
    """Guards the constant itself: a period short enough to fire between two polls would
    write into every healthy script that pauses to think."""
    assert QUIET_BEFORE_EOF_RESEND_SECONDS >= 1.0

    resender = _QuietEofResender(target, driverless=True)
    resender.consider()
    time.sleep(0.05)
    resender.consider()

    target.write_input.assert_not_called()


class _FakeBroker:
    """Stands in for TtyBroker; only `served_a_command` matters to the resender."""

    def __init__(self, served: bool = False) -> None:
        self.served_a_command = served


def test_a_broker_no_test_ever_used_still_gets_the_target_its_end_of_file(target):
    """The cli-password-manager regression.

    Attaching the broker suppresses the spawn-time VEOF. That is right for a test that
    drives the terminal and wrong for every conformance test that does not -- and the
    broker is attached to all of them. On the pipe backend those targets saw EOF at once;
    here they read a terminal that never ended and waited out the full timeout.
    cli-password-manager scores 15/16 on main and scored 1/16 here, fourteen renders
    running, because its target prompts for a master password.
    """
    resender = _QuietEofResender(target, driverless=False, broker=_FakeBroker(served=False))
    resender.consider()

    quiet_for(resender, QUIET_BEFORE_EOF_RESEND_SECONDS)

    target.write_input.assert_called_once_with(EOF_BYTE)


def test_a_terminal_a_test_is_driving_is_left_alone(target):
    """Once a test has spoken to the broker it owns the dialogue, and an unsolicited
    end-of-file would land in the middle of it."""
    resender = _QuietEofResender(target, driverless=False, broker=_FakeBroker(served=True))
    resender.consider()

    quiet_for(resender, QUIET_BEFORE_EOF_RESEND_SECONDS)

    target.write_input.assert_not_called()


def test_a_test_that_starts_driving_stops_further_deliveries(target):
    """The broker can go unused for a while and then be called, so the decision is re-made
    every poll rather than fixed at spawn."""
    broker = _FakeBroker(served=False)
    resender = _QuietEofResender(target, driverless=False, broker=broker)
    resender.consider()
    quiet_for(resender, QUIET_BEFORE_EOF_RESEND_SECONDS)
    target.write_input.assert_called_once_with(EOF_BYTE)
    target.write_input.reset_mock()

    broker.served_a_command = True
    quiet_for(resender, QUIET_BEFORE_EOF_RESEND_SECONDS)

    target.write_input.assert_not_called()
