"""Tests for the render trailer written to the log file.

The pretty exit summary goes out through Rich's print, which bypasses logging entirely,
so `codeplain.log` simply stopped at whatever was logged last. An artifact that ends
mid-render is indistinguishable from a process that died silently — and when a render
delivered a build with no entry point, that missing ending is exactly what blocked the
diagnosis.

The trailer is therefore both the fix and a probe: it is the last thing written on every
exit path, so an artifact without one proves the log was truncated rather than merely
uninformative.
"""

import logging
from unittest.mock import MagicMock

import pytest

from cli_output.render_summary import RENDER_TRAILER_PREFIX, log_render_trailer


def run_state(succeeded=True, cancelled=False):
    state = MagicMock()
    state.render_succeeded = succeeded
    state.render_cancelled = cancelled
    state.render_id = "5f1c25b7"
    state.rendered_functionalities = 22
    state.render_time_accumulated = 2934
    state.get_live_render_time.return_value = 2934
    state.render_generated_code_path = "/tmp/example-project/dist/"
    return state


@pytest.fixture
def trailer_lines(caplog):
    caplog.set_level(logging.INFO, logger="codeplain")

    def emit(state, spec="vault_cli.plain", error_message=None):
        caplog.clear()
        log_render_trailer(state, spec, error_message)
        return [record.getMessage() for record in caplog.records if RENDER_TRAILER_PREFIX in record.getMessage()]

    return emit


def test_a_completed_render_records_its_outcome(trailer_lines):
    lines = trailer_lines(run_state())

    assert len(lines) == 1
    assert "outcome=completed" in lines[0]


def test_the_trailer_carries_what_a_later_diagnosis_needs(trailer_lines):
    lines = trailer_lines(run_state())

    assert "render_id=5f1c25b7" in lines[0]
    assert "functionalities=22" in lines[0]
    assert "render_time_s=2934" in lines[0]
    assert "render_time_live_s=2934" in lines[0]
    assert "generated_code=/tmp/example-project/dist/" in lines[0]


def test_a_missing_generated_code_path_is_explicit(trailer_lines):
    """The run that delivered no entry point reported this field empty; it has to be
    legible in the log rather than a blank gap."""
    state = run_state()
    state.render_generated_code_path = None

    lines = trailer_lines(state)

    assert "generated_code=-" in lines[0]


def test_a_failed_render_records_the_reason(trailer_lines):
    lines = trailer_lines(run_state(succeeded=False), error_message="Conformance tests could not be fixed.")

    assert "outcome=failed" in lines[0]
    assert "error='Conformance tests could not be fixed.'" in lines[0]
    assert len(lines) == 1


def test_a_cancelled_render_is_not_reported_as_failed(trailer_lines):
    lines = trailer_lines(run_state(succeeded=False, cancelled=True))

    assert "outcome=cancelled" in lines[0]


def test_a_failure_without_a_message_still_ends_the_log(trailer_lines):
    lines = trailer_lines(run_state(succeeded=False))

    assert any("outcome=failed" in line for line in lines)


def test_the_trailer_is_flushed_so_it_survives_an_abrupt_exit():
    handler = MagicMock()
    handler.level = logging.NOTSET  # logging compares record.levelno against this
    logger = logging.getLogger("codeplain")
    logger.addHandler(handler)
    try:
        log_render_trailer(run_state(), "vault_cli.plain")
    finally:
        logger.removeHandler(handler)

    assert handler.flush.called


def test_a_render_that_raised_on_the_way_out_reports_the_reason_and_fails(trailer_lines):
    """A render can finish its functionalities and raise on the way out — publishing the
    build, for instance. That combination printed a success banner and logged no reason,
    which is how a failure on the publish path went unnoticed across a whole run.

    The reason belongs on the trailer, and so does an outcome that agrees with it: the
    success flag is set per module, so an earlier module's success outlives the failure of
    a later one, and a run that produced no build reported `outcome=completed`."""
    lines = trailer_lines(run_state(succeeded=True), error_message="The generated build references ...")

    assert any("error='The generated build references ...'" in line for line in lines)
    assert any("outcome=failed" in line for line in lines)
    assert not any("outcome=completed" in line for line in lines)


def test_a_multi_line_reason_still_leaves_the_trailer_on_one_line(trailer_lines):
    """Written raw, a newline would end the log on an indented continuation with no prefix
    — the shape of a truncated file."""
    lines = trailer_lines(run_state(succeeded=False), error_message="first line\nsecond line")

    assert len(lines) == 1
    assert "\n" not in lines[0]
    assert "outcome=failed" in lines[0]
    assert "second line" in lines[0]


class TestOutcomeAgreesWithTheReason:
    """A trailer that says `completed` next to an `error=` is worse than no trailer: it is
    read by tooling, and it was observed on a render that produced no code at all. The
    success flag is set per module, so an earlier module's success outlives the failure of
    a later one."""

    def test_success_with_no_error_still_completes(self, trailer_lines):
        assert "outcome=completed" in trailer_lines(run_state(succeeded=True))[0]

    def test_a_cancelled_render_reports_cancelled_not_failed(self, trailer_lines):
        lines = trailer_lines(run_state(succeeded=False, cancelled=True), error_message="Keyboard interrupt")

        assert "outcome=cancelled" in lines[0]
