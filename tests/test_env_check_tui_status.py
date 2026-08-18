"""The environment preflight as the user sees it: a transient row in the rendering status box."""

import asyncio

import pytest

from plain2code_events import EnvironmentCheckCompleted, EnvironmentCheckStarted
from plain2code_state import RunState
from tui import plain2code_tui as tui_module
from tui import widget_helpers
from tui.components import FRIDProgress, ProgressItem, TUIComponents
from tui.plain2code_tui import Plain2CodeTUI


class FakeProgressItem:
    def __init__(self):
        self.status = None
        self.removed = False

    def remove(self):
        self.removed = True


class FakeTUI:
    """Stands in for the app so the helpers can be exercised without a Textual runtime."""

    def __init__(self):
        self.item = FakeProgressItem()
        self.queried = []
        self.deferred = []

    def query_one(self, selector, _widget_type=None):
        self.queried.append(selector)
        return self.item

    def call_later(self, callback, *args):
        self.deferred.append((callback, args))
        if args:
            widget, status = args
            widget.status = status
        else:
            callback()


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


class TestTransientRow:
    def test_the_row_animates_while_the_check_runs(self):
        tui = FakeTUI()

        widget_helpers.display_environment_check_started(tui)

        # PROCESSING is the status ProgressItem renders with a running Spinner.
        assert tui.item.status == ProgressItem.PROCESSING
        assert tui.queried == [f"#{TUIComponents.ENVIRONMENT_CHECK_ITEM.value}"]

    def test_a_passing_check_takes_the_row_away(self):
        tui = FakeTUI()

        widget_helpers.display_environment_check_completed(tui, passed=True)

        # It runs once, so once it has passed it must not linger among the steps
        # that repeat for every functionality.
        assert tui.item.removed
        assert tui.item.status is None

    def test_a_failing_check_keeps_the_row(self):
        tui = FakeTUI()

        widget_helpers.display_environment_check_completed(tui, passed=False)

        # The row is the reason the render is about to stop, so it stays on screen.
        assert tui.item.status == ProgressItem.STOPPED
        assert not tui.item.removed

    def test_the_row_never_touches_the_bottom_status_line(self):
        tui = FakeTUI()

        widget_helpers.display_environment_check_started(tui)

        assert f"#{TUIComponents.RENDER_STATUS_WIDGET.value}" not in tui.queried


def progress_rows(show_environment_check: bool) -> list[str]:
    """Mount the dashboard and read the rendering status rows in the order they appear."""
    app = make_app(RecordingEventBus(), show_environment_check=show_environment_check)
    rows: list[str] = []

    async def scenario():
        async with app.run_test():
            rows.extend(item.id for item in app.query_one(FRIDProgress).query(ProgressItem))

    asyncio.run(scenario())
    return rows


class TestRowPlacement:
    def test_the_check_leads_the_rendering_status_rows(self):
        rows = progress_rows(show_environment_check=True)

        assert rows[0] == TUIComponents.ENVIRONMENT_CHECK_ITEM.value
        assert rows[1] == TUIComponents.FRID_PROGRESS_RENDER_FR.value

    def test_nothing_is_added_when_the_check_is_skipped(self):
        rows = progress_rows(show_environment_check=False)

        assert TUIComponents.ENVIRONMENT_CHECK_ITEM.value not in rows
        assert rows[0] == TUIComponents.FRID_PROGRESS_RENDER_FR.value

    def test_the_row_is_untouched_by_the_per_functionality_reset(self):
        """FridReadyHandler resets the rows that repeat; this one is not among them."""
        assert TUIComponents.ENVIRONMENT_CHECK_ITEM.value not in widget_helpers.FRID_PROGRESS_IDS


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

    def test_the_handlers_drive_the_panel(self, monkeypatch):
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
