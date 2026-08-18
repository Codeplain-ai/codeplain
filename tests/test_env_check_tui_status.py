"""The environment preflight as the user sees it in the TUI status line."""

import pytest

from plain2code_events import EnvironmentCheckCompleted, EnvironmentCheckStarted
from plain2code_state import RunState
from tui import plain2code_tui as tui_module
from tui import widget_helpers
from tui.components import TUIComponents
from tui.plain2code_tui import Plain2CodeTUI


class FakeStatusWidget:
    def __init__(self):
        self.text = ""
        self.classes_added = []

    def update(self, text):
        self.text = text

    def add_class(self, name):
        self.classes_added.append(name)


class FakeTUI:
    """Stands in for the app so the helpers can be exercised without a Textual runtime."""

    def __init__(self):
        self.widget = FakeStatusWidget()
        self.queried = []

    def query_one(self, selector, _widget_type=None):
        self.queried.append(selector)
        return self.widget


class RecordingEventBus:
    def __init__(self):
        self.subscriptions = []

    def subscribe(self, event_type, listener):
        self.subscriptions.append(event_type)

    def publish(self, event):
        pass


def make_app(event_bus) -> Plain2CodeTUI:
    return Plain2CodeTUI(
        event_bus=event_bus,
        run_state=RunState(spec_filename="test.plain"),
        on_ready=lambda: None,
        render_id="test-render-id",
        unittests_script=None,
        conformance_tests_script=None,
        prepare_environment_script=None,
        state_machine_version="0.0.0",
        css_path="styles.css",
    )


class TestStatusLine:
    def test_starting_the_check_replaces_the_rendering_message(self):
        tui = FakeTUI()

        widget_helpers.display_environment_check_started(tui)

        assert "Checking that this machine can build and test the project" in tui.widget.text
        assert tui.queried == [f"#{TUIComponents.RENDER_STATUS_WIDGET.value}"]

    def test_a_passing_check_hands_the_line_back_to_the_render(self):
        tui = FakeTUI()

        widget_helpers.display_environment_check_completed(tui, passed=True)

        assert "Rendering in progress" in tui.widget.text

    def test_a_failing_check_says_the_environment_is_not_ready(self):
        tui = FakeTUI()

        widget_helpers.display_environment_check_completed(tui, passed=False)

        assert "not ready to render" in tui.widget.text

    def test_the_status_line_never_says_the_render_started(self):
        """The whole point is that the TUI stops claiming to implement a functionality."""
        tui = FakeTUI()

        widget_helpers.display_environment_check_started(tui)

        assert "Implementing the functionality" not in tui.widget.text


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

    def test_the_handlers_drive_the_status_line(self, monkeypatch):
        calls = []
        monkeypatch.setattr(tui_module, "display_environment_check_started", lambda tui: calls.append("started"))
        monkeypatch.setattr(
            tui_module, "display_environment_check_completed", lambda tui, passed: calls.append(("done", passed))
        )

        app = make_app(RecordingEventBus())
        app.on_environment_check_started(EnvironmentCheckStarted())
        app.on_environment_check_completed(EnvironmentCheckCompleted(passed=False))

        assert calls == ["started", ("done", False)]

    def test_a_broken_status_widget_does_not_stop_the_render(self, monkeypatch):
        def explode(_tui):
            raise RuntimeError("widget is gone")

        monkeypatch.setattr(tui_module, "display_environment_check_started", explode)
        monkeypatch.setattr(tui_module, "log_to_widget", lambda *args, **kwargs: None)

        app = make_app(RecordingEventBus())
        app.on_environment_check_started(EnvironmentCheckStarted())
