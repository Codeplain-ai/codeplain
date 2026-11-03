"""Tests for --exit-when-done: the TUI closes by itself once rendering has stopped."""

import asyncio
import threading

from event_bus import EventBus
from plain2code_arguments import create_parser
from plain2code_events import RenderCompleted, RenderFailed
from plain2code_state import RunState
from tui.plain2code_tui import Plain2CodeTUI


def _make_app(run_state: RunState, event_bus: EventBus, exit_when_done: bool) -> Plain2CodeTUI:
    return Plain2CodeTUI(
        event_bus=event_bus,
        run_state=run_state,
        on_ready=lambda: None,
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


def test_option_is_off_by_default():
    args = create_parser().parse_args(["x.plain"])
    assert args.exit_when_done is False


def test_option_turns_on_from_the_command_line():
    args = create_parser().parse_args(["x.plain", "--exit-when-done"])
    assert args.exit_when_done is True
