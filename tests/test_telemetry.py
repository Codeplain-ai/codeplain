import sys
from argparse import Namespace

import pytest
import sentry_sdk
from sentry_sdk.envelope import Envelope
from sentry_sdk.transport import Transport

import plain2code_telemetry
from plain2code_state import RunState
from plain2code_telemetry import (
    NO_TELEMETRY_ENV_VAR,
    capture_crash,
    initialize_telemetry,
    telemetry_enabled,
)


class CaptureTransport(Transport):
    """Transport that records events instead of sending them over the network."""

    def __init__(self, options=None):
        super().__init__(options)
        self.events = []

    def capture_envelope(self, envelope: Envelope):
        event = envelope.get_event()
        if event is not None:
            self.events.append(event)


def make_exc_info(exception):
    try:
        raise exception
    except type(exception):
        return sys.exc_info()


def make_args(**overrides):
    args = Namespace(
        headless=False,
        unittests_script="run_unittests.sh",
        conformance_tests_script=None,
        prepare_environment_script=None,
    )
    for key, value in overrides.items():
        setattr(args, key, value)
    return args


@pytest.fixture(autouse=True)
def clean_telemetry_env(monkeypatch):
    """Ensure tests are not affected by the developer's environment and never send real events."""
    monkeypatch.delenv(NO_TELEMETRY_ENV_VAR, raising=False)
    monkeypatch.delenv(plain2code_telemetry.ENVIRONMENT_ENV_VAR, raising=False)
    yield
    client = sentry_sdk.get_client()
    if client.is_active():
        client.close(timeout=0)


@pytest.fixture
def transport():
    return CaptureTransport()


def init_with_transport(transport):
    assert initialize_telemetry(transport=transport)


def test_no_telemetry_env_var_disables(monkeypatch, transport):
    monkeypatch.setenv(NO_TELEMETRY_ENV_VAR, "1")

    assert not telemetry_enabled()
    assert not initialize_telemetry(transport=transport)
    assert not capture_crash(make_exc_info(KeyError("boom")), None, make_args())
    assert transport.events == []


def test_capture_crash_sends_event_with_tags(transport):
    init_with_transport(transport)

    run_state = RunState(spec_filename="test.plain")
    run_state.current_module = "my_module"
    run_state.current_frid = "2.1"
    run_state.current_render_state = "IMPLEMENTING_FRID"

    assert capture_crash(make_exc_info(KeyError("boom")), run_state, make_args(headless=True))
    sentry_sdk.flush(timeout=2)

    assert len(transport.events) == 1
    tags = transport.events[0]["tags"]
    assert tags["render_id"] == run_state.render_id
    assert tags["current_module"] == "my_module"
    assert tags["current_frid"] == "2.1"
    assert tags["render_state"] == "IMPLEMENTING_FRID"
    assert tags["headless"] is True
    assert tags["unittests_script_provided"] is True
    assert tags["conformance_tests_script_provided"] is False
    assert tags["prepare_environment_script_provided"] is False


def test_capture_crash_without_run_state(transport):
    init_with_transport(transport)

    assert capture_crash(make_exc_info(ValueError("boom")), None, make_args())
    sentry_sdk.flush(timeout=2)

    assert len(transport.events) == 1
    assert "render_id" not in transport.events[0]["tags"]


def test_local_variables_are_scrubbed(transport):
    init_with_transport(transport)

    def crash_with_sensitive_locals():
        api_key = "super-secret-key"  # noqa: F841
        plain_source = "proprietary spec content"  # noqa: F841
        raise KeyError("boom")

    try:
        crash_with_sensitive_locals()
    except KeyError:
        exc_info = sys.exc_info()

    assert capture_crash(exc_info, None, make_args())
    sentry_sdk.flush(timeout=2)

    frames = transport.events[0]["exception"]["values"][0]["stacktrace"]["frames"]
    crash_frame_vars = frames[-1]["vars"]
    assert crash_frame_vars["api_key"] == "[Filtered]"
    assert crash_frame_vars["plain_source"] == "[Filtered]"


def test_environment_defaults_to_production(transport):
    init_with_transport(transport)
    assert sentry_sdk.get_client().options["environment"] == "production"


def test_environment_env_var_respected(monkeypatch, transport):
    monkeypatch.setenv(plain2code_telemetry.ENVIRONMENT_ENV_VAR, "development")
    init_with_transport(transport)
    assert sentry_sdk.get_client().options["environment"] == "development"


def test_release_is_client_version(transport):
    from system_config import system_config

    init_with_transport(transport)
    assert sentry_sdk.get_client().options["release"] == system_config.client_version


def test_capture_crash_never_raises(monkeypatch):
    monkeypatch.setattr(
        sentry_sdk, "capture_exception", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("sdk broken"))
    )
    init_with_transport(CaptureTransport())

    assert capture_crash(make_exc_info(KeyError("boom")), None, make_args()) is False
