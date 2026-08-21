"""The preflight's place in the render: inside the render thread, visible in the TUI."""

import argparse
from unittest.mock import MagicMock

import pytest

import module_renderer as module_renderer_module
from env_check.types import SEVERITY_ERROR, SEVERITY_WARNING, STATUS_FAILED, CheckResult, CheckSpec, PreflightReport
from module_renderer import ModuleRenderer
from plain2code_events import EnvironmentCheckCompleted, EnvironmentCheckStarted
from plain2code_exceptions import EnvironmentCheckFailed


class RecordingEventBus:
    def __init__(self):
        self.published = []

    def publish(self, event):
        self.published.append(event)

    def subscribe(self, *_args, **_kwargs):
        pass


def make_renderer(published_events, **arg_overrides):
    args = argparse.Namespace(
        skip_env_check=False,
        verbose=False,
        force_render=False,
        render_machine_graph=False,
        copy_build=False,
    )
    for key, value in arg_overrides.items():
        setattr(args, key, value)

    return ModuleRenderer(
        codeplainAPI=MagicMock(),
        plain_module=MagicMock(),
        render_choice=None,
        render_range=None,
        args=args,
        run_state=MagicMock(),
        event_bus=published_events,
    )


def make_report(*, blocking: bool) -> PreflightReport:
    check = CheckSpec(
        id="java",
        type="command_available",
        severity=SEVERITY_ERROR if blocking else SEVERITY_WARNING,
        description="A Java 17 JDK is installed",
    )
    return PreflightReport(results=[CheckResult(check, STATUS_FAILED, "'javac' was not found on PATH")])


@pytest.fixture
def stub_preflight(monkeypatch):
    def _stub(report):
        monkeypatch.setattr(module_renderer_module, "run_environment_preflight", lambda *args, **kwargs: report)
        monkeypatch.setattr(module_renderer_module, "print_report", lambda *args, **kwargs: None)

    return _stub


def test_a_passing_preflight_brackets_itself_with_events(stub_preflight):
    stub_preflight(PreflightReport())
    event_bus = RecordingEventBus()

    make_renderer(event_bus)._verify_environment()

    assert isinstance(event_bus.published[0], EnvironmentCheckStarted)
    assert isinstance(event_bus.published[1], EnvironmentCheckCompleted)
    assert event_bus.published[1].passed is True


def test_a_blocking_preflight_reports_before_it_raises(stub_preflight):
    stub_preflight(make_report(blocking=True))
    event_bus = RecordingEventBus()

    with pytest.raises(EnvironmentCheckFailed):
        make_renderer(event_bus)._verify_environment()

    # The TUI must learn the outcome even though the render is about to stop.
    assert isinstance(event_bus.published[-1], EnvironmentCheckCompleted)
    assert event_bus.published[-1].passed is False


def test_warnings_alone_do_not_stop_the_render(stub_preflight):
    stub_preflight(make_report(blocking=False))
    event_bus = RecordingEventBus()

    make_renderer(event_bus)._verify_environment()

    assert event_bus.published[-1].passed is True


def test_skipping_the_check_publishes_nothing(stub_preflight):
    stub_preflight(make_report(blocking=True))
    event_bus = RecordingEventBus()

    make_renderer(event_bus, skip_env_check=True)._verify_environment()

    assert event_bus.published == []


def test_render_module_checks_the_environment_before_doing_any_work(monkeypatch, stub_preflight):
    stub_preflight(make_report(blocking=True))
    renderer = make_renderer(RecordingEventBus())

    rendered = []
    monkeypatch.setattr(
        ModuleRenderer, "_render_module", lambda self, *args, **kwargs: rendered.append(args) or (True, False)
    )

    with pytest.raises(EnvironmentCheckFailed):
        renderer.render_module()

    assert rendered == []


def test_an_unreachable_local_service_stops_the_render(monkeypatch):
    """A service the tests depend on is a blocking finding, not a note to skim past.

    The whole point of the preflight is that a render which cannot pass a single
    test never starts. This mirrors a real plan: the test requirements name a
    storage service on localhost:6001 that the tests do not mock, the service is
    down, and the render must stop with the reason rather than spend its budget
    failing every conformance test.
    """
    check = CheckSpec(
        id="storage-service",
        type="tcp_service_reachable",
        severity=SEVERITY_ERROR,
        description="Storage Service is reachable on port 6001",
        args={"host": "localhost", "port": 6001},
        reason="The test requirements say the tests talk to the storage service unmocked.",
        remediation={"default": "Start the storage service on localhost:6001."},
    )
    report = PreflightReport(
        results=[CheckResult(check, STATUS_FAILED, "localhost:6001 is not reachable ([Errno 61] Connection refused)")]
    )

    monkeypatch.setattr(module_renderer_module, "run_environment_preflight", lambda *a, **kw: report)
    monkeypatch.setattr(module_renderer_module, "print_report", lambda *a, **kw: None)

    event_bus = RecordingEventBus()
    with pytest.raises(EnvironmentCheckFailed) as raised:
        make_renderer(event_bus)._verify_environment()

    message = str(raised.value)
    assert "Storage Service is reachable on port 6001" in message
    assert "Start the storage service on localhost:6001." in message
    assert event_bus.published[-1].passed is False
