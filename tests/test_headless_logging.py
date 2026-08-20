"""Tests for what a headless render says while it is running.

Headless suppresses Rich output and attaches no TUI handler, so before this the only
sink was the log file — the process was silent on stdout for its entire run and a
CI job could not tell a render wedged for hours from a healthy one until the file was
collected at the end. One four-hour render produced 858 lines, all of them in its first
two minutes.

The second half of this file guards the hazard that adding a second handler exposes:
one record is handed to every handler in turn, so a formatter that rewrites
`record.msg` in place corrupts the output of the handlers after it.
"""

import logging
import sys
from unittest.mock import MagicMock

import pytest

from plain2code import setup_logging
from plain2code_console import console
from plain2code_logger import (
    LOGGER_NAME,
    ElapsedTimeFormatter,
    IndentedFormatter,
    stop_narrating,
)


@pytest.fixture
def run_state():
    state = MagicMock()
    state.get_live_render_time.return_value = 3661  # 01:01:01
    return state


def record(message, args=None):
    return logging.LogRecord(
        name="codeplain",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=message,
        args=args,
        exc_info=None,
    )


def test_the_elapsed_formatter_stamps_the_render_time(run_state):
    assert ElapsedTimeFormatter(run_state).format(record("hello")) == "[01:01:01] INFO codeplain: hello"


def test_continuation_lines_are_indented_past_the_timestamp(run_state):
    formatted = ElapsedTimeFormatter(run_state).format(record("first\nsecond"))

    assert formatted == "[01:01:01] INFO codeplain: first\n           second"


def test_formatting_twice_does_not_indent_twice(run_state):
    """Two handlers share one record. Formatting must be idempotent from the record's
    point of view, or the file log inherits the stdout log's indentation."""
    formatter = ElapsedTimeFormatter(run_state)
    entry = record("first\nsecond")

    first_pass = formatter.format(entry)
    second_pass = formatter.format(entry)

    assert first_pass == second_pass


def test_two_different_formatters_do_not_corrupt_each_other(run_state):
    """The real pairing in a headless render that also logs to a file."""
    entry = record("first\nsecond")

    IndentedFormatter("%(levelname)s:%(name)s:%(message)s").format(entry)
    elapsed = ElapsedTimeFormatter(run_state).format(entry)

    assert elapsed == "[01:01:01] INFO codeplain: first\n           second"


def test_the_indented_formatter_leaves_the_record_alone(run_state):
    entry = record("first\nsecond")

    IndentedFormatter("%(levelname)s:%(name)s:%(message)s").format(entry)

    assert entry.msg == "first\nsecond"


def test_arguments_are_interpolated_exactly_once(run_state):
    """The copy carries an already-interpolated message, so its args must be cleared —
    otherwise the parent formatter interpolates a second time and raises."""
    assert ElapsedTimeFormatter(run_state).format(record("value is %s", ("x",))).endswith("value is x")


@pytest.fixture
def configured_handlers(run_state):
    """setup_logging mutates the process-wide "codeplain" logger; restore it after."""
    logger = logging.getLogger(LOGGER_NAME)
    saved_handlers, saved_level = list(logger.handlers), logger.level

    def configure(headless):
        logger.handlers = []
        args = MagicMock()
        args.verbose = False
        args.logging_config_path = None
        setup_logging(args, MagicMock(), run_state, log_to_file=False, log_file_path="", headless=headless)
        return logger.handlers

    yield configure

    logger.handlers, logger.level = saved_handlers, saved_level


def stdout_handlers(handlers):
    return [h for h in handlers if type(h) is logging.StreamHandler and h.stream is sys.stdout]


def test_a_headless_render_narrates_to_stdout(configured_handlers):
    assert len(stdout_handlers(configured_handlers(headless=True))) == 1


def test_the_stdout_handler_carries_the_elapsed_time_format(configured_handlers):
    """It has to be readable as a render log, not just present."""
    handler = stdout_handlers(configured_handlers(headless=True))[0]

    assert isinstance(handler.formatter, ElapsedTimeFormatter)


def test_an_interactive_render_does_not_duplicate_output_on_stdout(configured_handlers):
    """The TUI already draws the log; a second copy on stdout would fight it for the
    terminal."""
    assert stdout_handlers(configured_handlers(headless=False)) == []


def test_the_formatter_survives_a_run_state_that_cannot_report_time():
    """A record can be logged before the render clock exists; it must still be readable."""
    broken = MagicMock()
    broken.get_live_render_time.side_effect = RuntimeError("no clock yet")

    assert ElapsedTimeFormatter(broken).format(record("hello")) == "[00:00:00] INFO codeplain: hello"


@pytest.fixture
def quiet_console():
    """Headless suppresses Rich output, so stdout carries the log sink and nothing else."""
    saved, console.quiet = console.quiet, True
    yield
    console.quiet = saved


def test_specification_text_reaches_the_stdout_sink(configured_handlers, quiet_console, capsys):
    """The narration carries the functional requirement, like every other record.

    These logs are the user's own, on the user's own machine, and the specification is
    theirs. Withholding it here buys nothing and costs them the detail that says which
    requirement a wedged render is stuck on. The rule this sink was briefly written
    against is about what *we* retain - see the Sentry and analytics scrubbers, which
    keep specification text out of anything leaving the machine."""
    configured_handlers(headless=True)

    console.info("Rendering functionality 3: the user's whole functional requirement")
    console.info("Running unit tests.")

    printed = capsys.readouterr().out
    assert "whole functional requirement" in printed
    assert "Running unit tests." in printed


def test_narration_stops_before_the_exit_summary(configured_handlers, quiet_console, capsys):
    """The summary bypasses logging, so a still-attached sink reports a failure twice."""
    configured_handlers(headless=True)
    capsys.readouterr()

    stop_narrating()
    console.info("this arrives after the render is over")

    assert "after the render is over" not in capsys.readouterr().out
