"""Product analytics via PostHog.

Events go to the same PostHog project the web app uses, keyed on the user's
email address. The web app identifies people by email too (posthog.identify in
codeplain-webapp), so a render lands on the same PostHog person as that user's
signup, plan selection and payment.

Consent is shared with crash reporting: CODEPLAIN_TELEMETRY governs both (see
plain2code_telemetry.telemetry_enabled). The email is the only identity the CLI
has; it never mints an anonymous id, so a run that cannot resolve the email
sends nothing.

The install scripts send their own "cli_installed" event with curl. This module
covers only the events the CLI itself emits.
"""

import os
import platform
from typing import Any, Optional

import requests

from plain2code_state import RunState
from plain2code_telemetry import telemetry_enabled
from system_config import system_config

# Both values are substituted at publish time by the publish-to-pypi workflow,
# from the repository's POSTHOG_PROJECT_TOKEN secret and POSTHOG_CAPTURE_URL
# variable. A source checkout keeps the placeholders below, so a run from a
# clone reports nothing unless the environment supplies both values.
POSTHOG_PROJECT_TOKEN = "__POSTHOG_PROJECT_TOKEN__"
POSTHOG_CAPTURE_URL = "__POSTHOG_CAPTURE_URL__"

PROJECT_TOKEN_ENV_VAR = "CODEPLAIN_POSTHOG_PROJECT_TOKEN"
CAPTURE_URL_ENV_VAR = "CODEPLAIN_POSTHOG_CAPTURE_URL"

CAPTURE_TIMEOUT_SECONDS = 2

RENDER_FINISHED_EVENT = "cli_render_finished"

OUTCOME_SUCCEEDED = "succeeded"
OUTCOME_CANCELLED = "cancelled"
OUTCOME_CRASHED = "crashed"
OUTCOME_FAILED = "failed"


def _posthog_config() -> Optional[tuple[str, str]]:
    """Return (capture_url, project_token), or None if analytics are unconfigured.

    A PostHog project token always starts with "phc_", so that prefix is also
    what tells a published build apart from a source checkout still carrying a
    placeholder. The environment wins over the built-in values, which is how a
    source checkout can report events at all.
    """
    token = os.environ.get(PROJECT_TOKEN_ENV_VAR, "").strip() or POSTHOG_PROJECT_TOKEN
    url = os.environ.get(CAPTURE_URL_ENV_VAR, "").strip() or POSTHOG_CAPTURE_URL

    if not token.startswith("phc_") or not url.startswith("https://"):
        return None

    return url, token


def _render_outcome(run_state: RunState, crashed: bool) -> str:
    """Classify the render the way the exit summary does, with crashes split out
    of the generic failure case."""
    if run_state.render_succeeded:
        return OUTCOME_SUCCEEDED
    if run_state.render_cancelled:
        return OUTCOME_CANCELLED
    if crashed:
        return OUTCOME_CRASHED
    return OUTCOME_FAILED


def _capture(config: tuple[str, str], event: str, distinct_id: str, properties: dict[str, Any]) -> bool:
    """Send a single event to PostHog. Returns True if PostHog accepted it."""
    capture_url, project_token = config
    payload: dict[str, Any] = {
        "api_key": project_token,
        "event": event,
        "distinct_id": distinct_id,
        "properties": {
            "$lib": "codeplain-cli",
            "$lib_version": system_config.client_version,
            **properties,
        },
    }
    response = requests.post(capture_url, json=payload, timeout=CAPTURE_TIMEOUT_SECONDS)
    return response.ok


def capture_render_finished(
    run_state: RunState,
    args,
    module_count: int,
    error_type: Optional[str] = None,
    crashed: bool = False,
) -> bool:
    """Report how a render ended. Returns True if an event was sent.

    Nothing is sent when analytics are unconfigured (a source checkout), when the
    user opted out, or when the run never learned who the user is (an invalid or
    missing API key fails before the connection check returns the email).

    No spec content, file paths or error messages are sent - only the exception
    class name, which cannot carry proprietary content.
    """
    if not telemetry_enabled():
        return False

    if not run_state.user_email:
        return False

    config = _posthog_config()
    if config is None:
        return False

    outcome = _render_outcome(run_state, crashed)

    try:
        return _capture(
            config,
            RENDER_FINISHED_EVENT,
            run_state.user_email,
            {
                "render_id": run_state.render_id,
                "outcome": outcome,
                "rendered_functionalities": run_state.rendered_functionalities,
                # Matches the duration the exit summary prints.
                "render_time_seconds": run_state.render_time_accumulated,
                "module_count": module_count,
                "client_version": system_config.client_version,
                "os": platform.system(),
                "headless": bool(getattr(args, "headless", False)),
                "unittests_script_provided": bool(getattr(args, "unittests_script", None)),
                "conformance_tests_script_provided": bool(getattr(args, "conformance_tests_script", None)),
                "prepare_environment_script_provided": bool(getattr(args, "prepare_environment_script", None)),
                # A cancel also unwinds through an exception; only report the
                # exception type when the render actually went wrong.
                "error_type": error_type if outcome in (OUTCOME_FAILED, OUTCOME_CRASHED) else None,
            },
        )
    except Exception:
        # Analytics must never break the CLI or mask the original error.
        return False
