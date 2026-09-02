"""Tests for reporting a render's terminal state to the API.

The render state machine lives in the client, so the API only learns how a
render ended because main() tells it. Two things are worth proving: that the
outcome names the right ending, and that main() reports on every path out -
including the ones the state machine never sees, like a keyboard interrupt.
"""

from argparse import Namespace
from unittest.mock import MagicMock, patch

import pytest

import plain2code
from plain2code_exceptions import InvalidAPIKey
from plain2code_state import RunState


def make_run_state(succeeded=False, cancelled=False, user_email="user@codeplain.ai"):
    run_state = RunState(spec_filename="project.plain")
    run_state.set_render_succeeded(succeeded)
    if cancelled:
        run_state.set_render_cancelled()
    run_state.user_email = user_email
    return run_state


class TestRenderOutcome:
    def test_a_successful_render_is_completed(self):
        assert plain2code._render_outcome(make_run_state(succeeded=True)) == "completed"

    def test_an_unsuccessful_render_is_failed(self):
        assert plain2code._render_outcome(make_run_state(succeeded=False)) == "failed"

    def test_a_cancelled_render_is_cancelled(self):
        """A cancelled render leaves render_succeeded False, so cancellation has to
        be asked about first or it would be reported as a failure."""
        assert plain2code._render_outcome(make_run_state(cancelled=True)) == "cancelled"

    def test_cancellation_wins_over_a_success_already_recorded(self):
        """Cancelling during the dist step leaves both flags set."""
        assert plain2code._render_outcome(make_run_state(succeeded=True, cancelled=True)) == "cancelled"


class TestReportRenderFinished:
    def test_reports_the_outcome_with_the_render_state(self):
        api = MagicMock()
        run_state = make_run_state(succeeded=True)

        plain2code.report_render_finished(api, run_state)

        api.render_finished.assert_called_once_with("completed", run_state)

    def test_says_nothing_when_the_api_never_identified_us(self):
        """user_email is set by the connection check, so an absent one means a
        missing key, an invalid key or an outdated client - none of which rendered."""
        api = MagicMock()

        plain2code.report_render_finished(api, make_run_state(user_email=None))

        api.render_finished.assert_not_called()

    @pytest.mark.parametrize(
        "failure",
        [
            ConnectionError("api unreachable"),
            TimeoutError("api slow"),
            RuntimeError("something else entirely"),
        ],
    )
    def test_a_failed_report_never_propagates(self, failure):
        """The report is the last thing a render does. It must not become the
        reason the render looks like it failed."""
        api = MagicMock()
        api.render_finished.side_effect = failure

        plain2code.report_render_finished(api, make_run_state(succeeded=True))


def make_args(api_key="test-key", headless=False):
    return Namespace(
        version=False,
        status=False,
        full_plain=False,
        dry_run=False,
        api_key=api_key,
        api="https://api.example.com",
        filename="project.plain",
        template_dir=None,
        build_folder="build",
        render_range=None,
        render_from=None,
        headless=headless,
        replay_with=None,
        log_to_file=False,
        log_file_name="codeplain.log",
        conformance_tests_script=None,
        unittests_script=None,
        prepare_environment_script=None,
    )


def run_main(render_side_effect=None, api_key="test-key"):
    """Drive main() with everything but the reporting path stubbed out.

    render() is replaced, so the stub is what decides how the render "ended" -
    which is the point: the report must be made from main()'s finally whatever
    render did, not from inside the state machine.

    Returns the API client mock so the caller can inspect the report.
    """
    api = MagicMock()

    def fake_render(plain_module, codeplainAPI, args, run_state, event_bus, default_log_level="INFO"):
        run_state.user_email = "user@codeplain.ai"
        if render_side_effect is not None:
            render_side_effect(run_state)

    with (
        patch("plain2code.parse_arguments", return_value=make_args(api_key=api_key)),
        patch("plain2code.file_utils.get_template_directories", return_value=["templates"]),
        patch("plain2code.plain_modules.PlainModule", return_value=MagicMock(all_required_modules=[])),
        patch("plain2code.setup_logging", return_value="INFO"),
        patch("plain2code.initialize_telemetry"),
        patch("plain2code.stop_narrating"),
        patch("plain2code.print_exit_summary"),
        patch("plain2code.dump_crash_logs"),
        patch("plain2code.capture_crash"),
        patch("plain2code.codeplain_api.CodeplainAPI", return_value=api),
        patch("plain2code.render", side_effect=fake_render),
    ):
        plain2code.main()

    return api


class TestMainReportsEveryEnding:
    """main()'s finally is the only place that sees all of these."""

    def test_a_completed_render_is_reported(self):
        api = run_main(lambda run_state: run_state.set_render_succeeded(True))

        assert api.render_finished.call_args.args[0] == "completed"

    def test_a_failed_render_is_reported(self):
        def fail(run_state):
            run_state.set_render_succeeded(False)
            raise Exception("rendering blew up")

        api = run_main(fail)

        assert api.render_finished.call_args.args[0] == "failed"

    def test_an_expected_error_is_reported_as_a_failure(self):
        def fail(run_state):
            raise InvalidAPIKey("key went stale mid-render")

        api = run_main(fail)

        assert api.render_finished.call_args.args[0] == "failed"

    def test_a_cancelled_render_is_reported(self):
        api = run_main(lambda run_state: run_state.set_render_cancelled())

        assert api.render_finished.call_args.args[0] == "cancelled"

    def test_a_keyboard_interrupt_is_reported(self):
        """Ctrl-C never enters a terminal state in the state machine, so this
        ending exists only in main()."""

        def interrupt(run_state):
            raise KeyboardInterrupt()

        api = run_main(interrupt)

        assert api.render_finished.call_args.args[0] == "failed"

    def test_a_missing_api_key_reports_nothing(self):
        """render() is never reached, so nothing identified the caller."""
        api = run_main(api_key=None)

        api.render_finished.assert_not_called()
