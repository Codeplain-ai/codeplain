"""Tests for --exit-when-done: the TUI closes by itself once rendering has stopped."""

import asyncio
import threading

import pytest

from event_bus import EventBus
from plain2code_arguments import create_parser, parse_arguments
from plain2code_events import LogMessageEmitted, RenderCompleted, RenderFailed
from plain2code_state import RunState
from tui.plain2code_tui import Plain2CodeTUI


def _make_app(run_state: RunState, event_bus: EventBus, exit_when_done: bool, on_ready=lambda: None) -> Plain2CodeTUI:
    return Plain2CodeTUI(
        event_bus=event_bus,
        run_state=run_state,
        on_ready=on_ready,
        render_id="test-render-id",
        unittests_script=None,
        conformance_tests_script=None,
        prepare_environment_script=None,
        state_machine_version="0.0.0",
        exit_when_done=exit_when_done,
        css_path="styles.css",
    )


def _run(exit_when_done: bool, event) -> bool:
    """Publish ``event`` to a mounted TUI and report whether the TUI closed itself."""

    async def scenario() -> bool:
        event_bus = EventBus()
        run_state = RunState(spec_filename="x.plain")
        app = _make_app(run_state, event_bus, exit_when_done)
        async with app.run_test() as pilot:
            event_bus.publish(event)
            await pilot.pause()
            return app._exit

    return asyncio.run(scenario())


def test_tui_closes_itself_when_rendering_completes():
    assert _run(True, RenderCompleted(rendered_code_path="plain_modules/x/")) is True


def test_tui_closes_itself_when_rendering_fails():
    assert _run(True, RenderFailed(error_message="Conformance tests failed")) is True


def test_tui_keeps_the_failure_reason_for_the_summary():
    """The TUI closes right away, so the reason it showed must survive on the run state."""

    async def scenario() -> RunState:
        event_bus = EventBus()
        run_state = RunState(spec_filename="x.plain")
        app = _make_app(run_state, event_bus, True)
        async with app.run_test() as pilot:
            event_bus.publish(RenderFailed(error_message="Conformance tests failed"))
            await pilot.pause()
        return run_state

    assert asyncio.run(scenario()).render_error_message == "Conformance tests failed"


def test_tui_waits_for_the_user_without_the_option():
    assert _run(False, RenderCompleted(rendered_code_path="plain_modules/x/")) is False
    assert _run(False, RenderFailed(error_message="Conformance tests failed")) is False


def test_tui_closes_itself_when_the_render_thread_reports_completion():
    """The render runs in its own thread, so the completion event reaches the TUI from there."""

    async def scenario() -> bool:
        event_bus = EventBus()
        run_state = RunState(spec_filename="x.plain")
        app = _make_app(run_state, event_bus, True)
        async with app.run_test() as pilot:
            thread = threading.Thread(
                target=lambda: event_bus.publish(RenderCompleted(rendered_code_path="plain_modules/x/"))
            )
            thread.start()
            thread.join()
            await pilot.pause()
            return app._exit

    assert asyncio.run(scenario()) is True


def _log(message: str) -> LogMessageEmitted:
    return LogMessageEmitted(logger_name="codeplain", level="INFO", message=message, timestamp="00:00:01")


@pytest.mark.parametrize(
    "event", [RenderCompleted(rendered_code_path="plain_modules/x/"), RenderFailed(error_message="x")]
)
def test_tui_closes_cleanly_while_log_lines_are_still_arriving(event):
    """Log lines reach the TUI from the render thread right up to (and past) the final event.

    Entries still waiting to be drawn when the TUI closes must not crash it: before the fix,
    mounting them into the torn-down log view raised MountError, which run_test re-raises.
    """

    async def scenario() -> bool:
        event_bus = EventBus()
        run_state = RunState(spec_filename="x.plain")

        def render():
            for i in range(20):
                event_bus.publish(_log(f"step {i}"))
            event_bus.publish(event)
            for i in range(20):
                event_bus.publish(_log(f"after {i}"))

        # Started from on_ready, exactly as plain2code.render() starts the render thread.
        render_thread = threading.Thread(target=render, daemon=True)
        app = _make_app(run_state, event_bus, True, on_ready=render_thread.start)
        async with app.run_test() as pilot:
            for _ in range(100):
                if app._exit and not render_thread.is_alive():
                    break
                await pilot.pause(0.01)
            return app._exit

    assert asyncio.run(scenario()) is True


def test_option_is_off_by_default():
    args = create_parser().parse_args(["x.plain"])
    assert args.exit_when_done is False


def test_option_turns_on_from_the_command_line():
    args = create_parser().parse_args(["x.plain", "--exit-when-done"])
    assert args.exit_when_done is True


def test_option_turns_off_from_the_command_line():
    args = create_parser().parse_args(["x.plain", "--no-exit-when-done"])
    assert args.exit_when_done is False


def test_command_line_turns_off_an_exit_configured_in_config_yaml(tmp_path, monkeypatch):
    (tmp_path / "x.plain").write_text("")
    (tmp_path / "config.yaml").write_text("exit-when-done: true\n")
    monkeypatch.chdir(tmp_path)

    assert parse_arguments([str(tmp_path / "x.plain")]).exit_when_done is True
    assert parse_arguments([str(tmp_path / "x.plain"), "--no-exit-when-done"]).exit_when_done is False
