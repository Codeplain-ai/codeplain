from argparse import Namespace

import pytest

import analytics
import plain2code_telemetry
from analytics import (
    CAPTURE_URL_ENV_VAR,
    OUTCOME_CANCELLED,
    OUTCOME_CRASHED,
    OUTCOME_FAILED,
    OUTCOME_SUCCEEDED,
    PROJECT_TOKEN_ENV_VAR,
    RENDER_FINISHED_EVENT,
    capture_render_finished,
)
from plain2code_state import RunState
from plain2code_telemetry import TELEMETRY_ENV_VAR

# The token and host are placeholders in a source checkout; the publish workflow
# substitutes them. Tests supply them the way a developer would - via the
# environment.
TEST_TOKEN = "phc_testtoken"
TEST_URL = "https://eu.i.posthog.com/i/v0/e/"


class FakeResponse:
    def __init__(self, ok=True):
        self.ok = ok


@pytest.fixture(autouse=True)
def analytics_env(monkeypatch):
    """Put the module in the state a published install would be in.

    Tests run from a source checkout, where telemetry is off by default and the
    PostHog placeholders are unsubstituted; pretend to be production and supply
    the config through the environment so the default path is covered.
    """
    monkeypatch.delenv(TELEMETRY_ENV_VAR, raising=False)
    monkeypatch.setattr(plain2code_telemetry.system_config, "environment", "production")
    monkeypatch.setenv(PROJECT_TOKEN_ENV_VAR, TEST_TOKEN)
    monkeypatch.setenv(CAPTURE_URL_ENV_VAR, TEST_URL)


@pytest.fixture
def sent(monkeypatch):
    """Record capture requests instead of sending them over the network."""
    requests = []

    def fake_post(url, json=None, timeout=None):
        requests.append({"url": url, "payload": json, "timeout": timeout})
        return FakeResponse()

    monkeypatch.setattr(analytics.requests, "post", fake_post)
    return requests


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


def make_run_state(**overrides):
    run_state = RunState(spec_filename="test.plain")
    run_state.user_email = "user@codeplain.ai"
    for key, value in overrides.items():
        setattr(run_state, key, value)
    return run_state


def test_render_finished_event_is_sent_with_properties(sent):
    run_state = make_run_state(render_succeeded=True, rendered_functionalities=4, render_time_accumulated=125)

    assert capture_render_finished(run_state, make_args(headless=True), module_count=3)

    assert len(sent) == 1
    payload = sent[0]["payload"]
    assert sent[0]["url"] == TEST_URL
    assert payload["api_key"] == TEST_TOKEN
    assert payload["event"] == RENDER_FINISHED_EVENT
    # The web app identifies people by email too, so CLI and web events land on
    # the same PostHog person.
    assert payload["distinct_id"] == "user@codeplain.ai"

    properties = payload["properties"]
    assert properties["render_id"] == run_state.render_id
    assert properties["outcome"] == OUTCOME_SUCCEEDED
    assert properties["rendered_functionalities"] == 4
    assert properties["render_time_seconds"] == 125
    assert properties["module_count"] == 3
    assert properties["headless"] is True
    assert properties["unittests_script_provided"] is True
    assert properties["conformance_tests_script_provided"] is False
    assert properties["prepare_environment_script_provided"] is False
    assert properties["error_type"] is None
    assert properties["$lib"] == "codeplain-cli"


def test_no_spec_content_is_sent(sent):
    """Only the exception class name may describe a failure - never the message,
    the spec filename or a path."""
    run_state = make_run_state(spec_filename="/home/user/secret_project.plain")

    assert capture_render_finished(run_state, make_args(), module_count=1, error_type="PlainSyntaxError")

    properties = sent[0]["payload"]["properties"]
    assert "secret_project" not in str(properties)
    assert properties["error_type"] == "PlainSyntaxError"


@pytest.mark.parametrize(
    "state, crashed, expected",
    [
        ({"render_succeeded": True}, False, OUTCOME_SUCCEEDED),
        ({"render_cancelled": True}, False, OUTCOME_CANCELLED),
        # A cancel unwinds through SystemExit, which is not a crash.
        ({"render_cancelled": True}, True, OUTCOME_CANCELLED),
        ({}, True, OUTCOME_CRASHED),
        ({}, False, OUTCOME_FAILED),
    ],
)
def test_outcome_classification(sent, state, crashed, expected):
    assert capture_render_finished(make_run_state(**state), make_args(), module_count=1, crashed=crashed)

    assert sent[0]["payload"]["properties"]["outcome"] == expected


def test_error_type_is_dropped_for_a_cancelled_render(sent):
    run_state = make_run_state(render_cancelled=True)

    assert capture_render_finished(run_state, make_args(), module_count=1, error_type="SystemExit")

    assert sent[0]["payload"]["properties"]["error_type"] is None


@pytest.mark.parametrize("value", ["0", "false", "off", "OFF", " False "])
def test_opt_out_sends_nothing(monkeypatch, sent, value):
    monkeypatch.setenv(TELEMETRY_ENV_VAR, value)

    assert not capture_render_finished(make_run_state(), make_args(), module_count=1)
    assert sent == []


def test_disabled_outside_production(monkeypatch, sent):
    monkeypatch.setattr(plain2code_telemetry.system_config, "environment", "development")

    assert not capture_render_finished(make_run_state(), make_args(), module_count=1)
    assert sent == []


def test_nothing_is_sent_without_a_user_email(sent):
    """The email is the only identity the CLI has; a run that never resolved it
    (invalid or missing API key) is not reported."""
    run_state = make_run_state(user_email=None)

    assert not capture_render_finished(run_state, make_args(), module_count=1)
    assert sent == []


def test_nothing_is_sent_from_an_unconfigured_checkout(monkeypatch, sent):
    """A source checkout carries unsubstituted placeholders. It must report
    nothing rather than post to a nonexistent endpoint."""
    monkeypatch.delenv(PROJECT_TOKEN_ENV_VAR)
    monkeypatch.delenv(CAPTURE_URL_ENV_VAR)

    assert not analytics.POSTHOG_PROJECT_TOKEN.startswith("phc_"), "a real token must never be committed"
    assert not capture_render_finished(make_run_state(), make_args(), module_count=1)
    assert sent == []


@pytest.mark.parametrize(
    "token, url",
    [
        ("__POSTHOG_PROJECT_TOKEN__", TEST_URL),
        ("not-a-posthog-token", TEST_URL),
        ("", TEST_URL),
        (TEST_TOKEN, "__POSTHOG_CAPTURE_URL__"),
        (TEST_TOKEN, "http://insecure.example.com/"),
        (TEST_TOKEN, ""),
    ],
)
def test_a_half_substituted_config_sends_nothing(monkeypatch, sent, token, url):
    monkeypatch.setenv(PROJECT_TOKEN_ENV_VAR, token)
    monkeypatch.setenv(CAPTURE_URL_ENV_VAR, url)

    assert not capture_render_finished(make_run_state(), make_args(), module_count=1)
    assert sent == []


def test_capture_never_raises(monkeypatch):
    def broken_post(*args, **kwargs):
        raise RuntimeError("network on fire")

    monkeypatch.setattr(analytics.requests, "post", broken_post)

    assert capture_render_finished(make_run_state(), make_args(), module_count=1) is False


def test_capture_uses_a_short_timeout(sent):
    """A render can end on Ctrl-C; analytics must not hold up the exit."""
    assert capture_render_finished(make_run_state(), make_args(), module_count=1)

    assert sent[0]["timeout"] == analytics.CAPTURE_TIMEOUT_SECONDS
    assert analytics.CAPTURE_TIMEOUT_SECONDS <= 2
