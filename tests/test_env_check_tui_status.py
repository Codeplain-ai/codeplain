"""The environment preflight as the user sees it: a row in the FRID progress box."""

import asyncio

import pytest

from plain2code_events import EnvironmentCheckCompleted, EnvironmentCheckStarted
from plain2code_state import RunState
from tui import plain2code_tui as tui_module
from tui import widget_helpers
from tui.components import ProgressItem, TUIComponents
from tui.plain2code_tui import Plain2CodeTUI


class FakeProgressItem:
    def __init__(self):
        self.status = None


class FakeTUI:
    """Stands in for the app so the helpers can be exercised without a Textual runtime."""

    def __init__(self):
        self.item = FakeProgressItem()
        self.queried = []
        self.deferred = []

    def query_one(self, selector, _widget_type=None):
        self.queried.append(selector)
        return self.item

    def call_later(self, callback, widget, status):
        self.deferred.append((callback, widget, status))
        widget.status = status


class RecordingEventBus:
    def __init__(self):
        self.subscriptions = []

    def subscribe(self, event_type, listener):
        self.subscriptions.append(event_type)

    def publish(self, event):
        pass


def make_app(event_bus, show_environment_check: bool = True) -> Plain2CodeTUI:
    return Plain2CodeTUI(
        event_bus=event_bus,
        run_state=RunState(spec_filename="test.plain"),
        on_ready=lambda: None,
        render_id="test-render-id",
        unittests_script=None,
        conformance_tests_script=None,
        prepare_environment_script=None,
        show_environment_check=show_environment_check,
        state_machine_version="0.0.0",
        css_path="styles.css",
    )


class TestProgressRow:
    def test_the_row_animates_while_the_check_runs(self):
        tui = FakeTUI()

        widget_helpers.display_environment_check_started(tui)

        # PROCESSING is the status ProgressItem renders with a running Spinner.
        assert tui.item.status == ProgressItem.PROCESSING
        assert tui.queried == [f"#{TUIComponents.FRID_PROGRESS_ENV_CHECK.value}"]

    def test_a_passing_check_completes_the_row(self):
        tui = FakeTUI()

        widget_helpers.display_environment_check_completed(tui, passed=True)

        assert tui.item.status == ProgressItem.COMPLETED

    def test_a_failing_check_stops_the_row(self):
        tui = FakeTUI()

        widget_helpers.display_environment_check_completed(tui, passed=False)

        assert tui.item.status == ProgressItem.STOPPED

    def test_the_row_never_touches_the_bottom_status_line(self):
        tui = FakeTUI()

        widget_helpers.display_environment_check_started(tui)

        assert f"#{TUIComponents.RENDER_STATUS_WIDGET.value}" not in tui.queried


def progress_row_ids(show_environment_check: bool) -> list[str]:
    """Mount the dashboard and read the progress rows in the order they appear."""
    app = make_app(RecordingEventBus(), show_environment_check=show_environment_check)
    row_ids: list[str] = []

    async def scenario():
        async with app.run_test():
            row_ids.extend(item.id for item in app.query(ProgressItem))

    asyncio.run(scenario())
    return row_ids


class TestRowVisibility:
    def test_the_row_is_the_first_step_shown(self):
        row_ids = progress_row_ids(show_environment_check=True)

        assert row_ids[0] == TUIComponents.FRID_PROGRESS_ENV_CHECK.value
        assert TUIComponents.FRID_PROGRESS_RENDER_FR.value in row_ids

    def test_there_is_no_row_when_the_check_is_skipped(self):
        row_ids = progress_row_ids(show_environment_check=False)

        assert TUIComponents.FRID_PROGRESS_ENV_CHECK.value not in row_ids
        assert TUIComponents.FRID_PROGRESS_RENDER_FR.value in row_ids

    def test_the_row_survives_the_first_functionality_starting(self):
        """FridReadyHandler resets the per-functionality rows; this one is not among them."""
        assert TUIComponents.FRID_PROGRESS_ENV_CHECK.value not in widget_helpers.FRID_PROGRESS_IDS


class TestAppWiring:
    def test_the_app_subscribes_to_both_preflight_events(self, monkeypatch):
        event_bus = RecordingEventBus()
        app = make_app(event_bus)

        # on_mount registers every subscription first, then reaches for the Textual
        # runtime, which is not running here. Cut it off at that boundary.
        class ReachedTheRuntime(Exception):
            pass

        def stop(*_args, **_kwargs):
            raise ReachedTheRuntime()

        monkeypatch.setattr(type(app), "set_interval", stop, raising=False)

        with pytest.raises(ReachedTheRuntime):
            app.on_mount()

        assert EnvironmentCheckStarted in event_bus.subscriptions
        assert EnvironmentCheckCompleted in event_bus.subscriptions

    def test_the_handlers_drive_the_progress_row(self, monkeypatch):
        calls = []
        monkeypatch.setattr(tui_module, "display_environment_check_started", lambda tui: calls.append("started"))
        monkeypatch.setattr(
            tui_module, "display_environment_check_completed", lambda tui, passed: calls.append(("done", passed))
        )

        app = make_app(RecordingEventBus())
        app.on_environment_check_started(EnvironmentCheckStarted())
        app.on_environment_check_completed(EnvironmentCheckCompleted(passed=False))

        assert calls == ["started", ("done", False)]

    def test_a_broken_widget_does_not_stop_the_render(self, monkeypatch):
        def explode(_tui):
            raise RuntimeError("widget is gone")

        monkeypatch.setattr(tui_module, "display_environment_check_started", explode)
        monkeypatch.setattr(tui_module, "log_to_widget", lambda *args, **kwargs: None)

        app = make_app(RecordingEventBus())
        app.on_environment_check_started(EnvironmentCheckStarted())
