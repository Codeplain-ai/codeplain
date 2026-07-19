"""Headless TUI tests for the live credit-usage line and terminal summaries."""

import asyncio
import time

from textual.widgets import Static

from event_bus import EventBus
from plain2code_events import RenderCompleted, RenderFailed
from plain2code_state import RunState
from tui.components import TUIComponents
from tui.plain2code_tui import Plain2CodeTUI


def _make_app(run_state: RunState, event_bus: EventBus) -> Plain2CodeTUI:
    return Plain2CodeTUI(
        event_bus=event_bus,
        run_state=run_state,
        on_ready=lambda: None,
        render_id="test-render-id",
        unittests_script=None,
        conformance_tests_script=None,
        prepare_environment_script=None,
        state_machine_version="0.0.0",
        css_path="styles.css",
    )


def _set_live_render_time(run_state: RunState, seconds: int) -> None:
    """Make run_state.get_live_render_time() return ``seconds`` deterministically.

    Models an in-progress segment (nothing banked yet) that started ``seconds`` ago,
    which is the state on the exception/cancel paths the render machine never
    finalizes.
    """
    run_state.render_time_accumulated = 0
    run_state.last_render_start_timestamp = time.monotonic() - seconds


def _usage_text(app: Plain2CodeTUI) -> str:
    widget = app.query_one(f"#{TUIComponents.RENDER_USAGE_WIDGET.value}", Static)
    return str(widget.content)


def _status_text(app: Plain2CodeTUI) -> str:
    widget = app.query_one(f"#{TUIComponents.RENDER_STATUS_WIDGET.value}", Static)
    return str(widget.content)


def test_usage_line_reflects_live_progress():
    async def scenario():
        event_bus = EventBus()
        run_state = RunState(spec_filename="x.plain")
        app = _make_app(run_state, event_bus)
        async with app.run_test() as pilot:
            run_state.rendered_functionalities = 3
            _set_live_render_time(run_state, 349)
            app._refresh_usage_summary()
            await pilot.pause()

            text = _usage_text(app)
            assert "functionalities  [#FFFFFF]3" in text
            assert "used credits  [#FFFFFF]3" in text
            assert "render time  [#FFFFFF]5m 49s" in text

    asyncio.run(scenario())


def test_usage_line_frozen_while_paused():
    async def scenario():
        event_bus = EventBus()
        run_state = RunState(spec_filename="x.plain")
        app = _make_app(run_state, event_bus)
        async with app.run_test():
            _set_live_render_time(run_state, 10)
            app._refresh_usage_summary()
            assert "render time  [#FFFFFF]10s" in _usage_text(app)

            # While paused, a refresh must not sample the render time again.
            app._usage_paused = True
            _set_live_render_time(run_state, 999)  # 16m 39s
            app._refresh_usage_summary()
            assert "render time  [#FFFFFF]10s" in _usage_text(app)
            assert "16m 39s" not in _usage_text(app)

            # Resuming samples again.
            app._usage_paused = False
            app._refresh_usage_summary()
            assert "render time  [#FFFFFF]16m 39s" in _usage_text(app)

    asyncio.run(scenario())


def test_usage_line_finalized_on_success():
    async def scenario():
        event_bus = EventBus()
        run_state = RunState(spec_filename="x.plain")
        app = _make_app(run_state, event_bus)
        async with app.run_test() as pilot:
            run_state.rendered_functionalities = 6
            _set_live_render_time(run_state, 349)
            event_bus.publish(RenderCompleted(rendered_code_path="plain_modules/hello_world_python/"))
            await pilot.pause()

            status = _status_text(app)
            assert "rendering completed!" in status
            assert "generated code folder: plain_modules/hello_world_python/" in status

            usage = _usage_text(app)
            assert "functionalities  [#FFFFFF]6" in usage
            assert "used credits  [#FFFFFF]6" in usage
            assert "render time  [#FFFFFF]5m 49s" in usage
            assert app._render_finished is True
            # The live value is captured onto the run state so the console summary matches.
            assert run_state.render_time_accumulated == 349

    asyncio.run(scenario())


def test_usage_line_below_error_on_failure():
    async def scenario():
        event_bus = EventBus()
        run_state = RunState(spec_filename="x.plain")
        app = _make_app(run_state, event_bus)
        async with app.run_test() as pilot:
            # Failure after some functionalities and elapsed time. The render machine
            # did NOT finalize run_state.render_time_accumulated (exception failure
            # path), so the shared live value must supply the real elapsed time.
            run_state.rendered_functionalities = 2
            _set_live_render_time(run_state, 349)

            error = "Conformance tests failed for functionality 3"
            event_bus.publish(RenderFailed(error_message=error))
            await pilot.pause()

            status = _status_text(app)
            assert error in status

            usage = _usage_text(app)
            assert "functionalities  [#FFFFFF]2" in usage
            assert "used credits  [#FFFFFF]2" in usage
            # Regression: this was "0s" because the summary trusted the unfinalized state.
            assert "render time  [#FFFFFF]5m 49s" in usage
            assert app._render_finished is True
            assert run_state.render_time_accumulated == 349

    asyncio.run(scenario())


def test_usage_line_zero_when_failure_before_any_progress():
    async def scenario():
        event_bus = EventBus()
        run_state = RunState(spec_filename="x.plain")
        app = _make_app(run_state, event_bus)
        async with app.run_test() as pilot:
            # Failure before any functionality/time (e.g. a syntax error at parse time).
            _set_live_render_time(run_state, 0)
            error = "Syntax error at line 1: Invalid specification heading (`implementation req`)"
            event_bus.publish(RenderFailed(error_message=error))
            await pilot.pause()

            usage = _usage_text(app)
            assert "functionalities  [#FFFFFF]0" in usage
            assert "used credits  [#FFFFFF]0" in usage
            assert "render time  [#FFFFFF]0s" in usage

    asyncio.run(scenario())


def test_usage_line_stops_updating_after_completion():
    async def scenario():
        event_bus = EventBus()
        run_state = RunState(spec_filename="x.plain")
        app = _make_app(run_state, event_bus)
        async with app.run_test() as pilot:
            run_state.rendered_functionalities = 2
            _set_live_render_time(run_state, 5)
            event_bus.publish(RenderCompleted(rendered_code_path="plain_modules/x/"))
            await pilot.pause()

            # Late mutation must not change the frozen final usage line.
            run_state.rendered_functionalities = 99
            app._refresh_usage_summary()
            await pilot.pause()

            usage = _usage_text(app)
            assert "functionalities  [#FFFFFF]2" in usage
            assert "functionalities  [#FFFFFF]99" not in usage

    asyncio.run(scenario())


def test_cancel_records_render_time_for_summary():
    async def scenario():
        event_bus = EventBus()
        run_state = RunState(spec_filename="x.plain")
        app = _make_app(run_state, event_bus)
        app._on_cancel = run_state.set_render_cancelled
        async with app.run_test():
            # The render machine never finalizes render_time_accumulated on cancel,
            # so the TUI must capture the live value for the summary.
            run_state.rendered_functionalities = 2
            _set_live_render_time(run_state, 42)
            app.action_quit()

            assert run_state.render_cancelled is True
            assert run_state.render_time_accumulated == 42

    asyncio.run(scenario())
