"""The environment preflight as the user sees it: its own one-time render-setup panel."""

import asyncio

import pytest

from plain2code_events import EnvironmentCheckCompleted, EnvironmentCheckStarted
from plain2code_state import RunState
from tui import plain2code_tui as tui_module
from tui import widget_helpers
from tui.components import EnvironmentCheckProgress, FRIDProgress, ProgressItem, TUIComponents
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


class TestSetupPanel:
    def test_the_panel_animates_while_the_check_runs(self):
        tui = FakeTUI()

        widget_helpers.display_environment_check_started(tui)

        # PROCESSING is the status ProgressItem renders with a running Spinner.
        assert tui.item.status == ProgressItem.PROCESSING
        assert tui.queried == [f"#{TUIComponents.ENVIRONMENT_CHECK_ITEM.value}"]

    def test_a_passing_check_ticks_the_panel(self):
        tui = FakeTUI()

        widget_helpers.display_environment_check_completed(tui, passed=True)

        assert tui.item.status == ProgressItem.COMPLETED

    def test_a_failing_check_stops_the_panel(self):
        tui = FakeTUI()

        widget_helpers.display_environment_check_completed(tui, passed=False)

        assert tui.item.status == ProgressItem.STOPPED

    def test_the_panel_never_touches_the_bottom_status_line(self):
        tui = FakeTUI()

        widget_helpers.display_environment_check_started(tui)

        assert f"#{TUIComponents.RENDER_STATUS_WIDGET.value}" not in tui.queried


def mounted_dashboard(show_environment_check: bool):
    """Mount the dashboard and return (environment-check panel or None, FRID progress rows)."""
    app = make_app(RecordingEventBus(), show_environment_check=show_environment_check)
    found: dict = {}

    async def scenario():
        async with app.run_test():
            panels = list(app.query(EnvironmentCheckProgress))
            found["panel"] = panels[0] if panels else None
            frid_progress = app.query_one(FRIDProgress)
            found["frid_rows"] = [item.id for item in frid_progress.query(ProgressItem)]

    asyncio.run(scenario())
    return found["panel"], found["frid_rows"]


class TestPanelPlacement:
    def test_the_check_is_not_one_of_the_per_functionality_steps(self):
        """It runs once per render, so it must not sit among the steps that repeat."""
        _, frid_rows = mounted_dashboard(show_environment_check=True)

        assert TUIComponents.ENVIRONMENT_CHECK_ITEM.value not in frid_rows
        assert frid_rows[0] == TUIComponents.FRID_PROGRESS_RENDER_FR.value

    def test_the_check_gets_a_panel_of_its_own(self):
        panel, _ = mounted_dashboard(show_environment_check=True)

        assert panel is not None
        assert panel.id == TUIComponents.ENVIRONMENT_CHECK.value

    def test_there_is_no_panel_when_the_check_is_skipped(self):
        panel, frid_rows = mounted_dashboard(show_environment_check=False)

        assert panel is None
        assert frid_rows[0] == TUIComponents.FRID_PROGRESS_RENDER_FR.value

    def test_the_panel_is_untouched_by_the_per_functionality_reset(self):
        """FridReadyHandler resets the per-functionality rows; this one is not among them."""
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
