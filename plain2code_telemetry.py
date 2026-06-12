"""Crash reporting via Sentry.

Only unexpected exceptions are reported (the caller decides which exceptions are
expected; see EXPECTED_EXCEPTIONS in plain2code.py). Reporting is on by default
and can be disabled by setting the CODEPLAIN_NO_TELEMETRY environment variable
to any non-empty value.
"""

import os
from typing import Any, Optional

import sentry_sdk
from sentry_sdk.integrations.atexit import AtexitIntegration
from sentry_sdk.integrations.dedupe import DedupeIntegration
from sentry_sdk.integrations.modules import ModulesIntegration
from sentry_sdk.scrubber import DEFAULT_DENYLIST, EventScrubber

from plain2code_state import RunState
from system_config import system_config

SENTRY_DSN = "https://64d0d86b50b34e2dede3e4eaf5142282@o4510793955934208.ingest.us.sentry.io/4511540621213696"

NO_TELEMETRY_ENV_VAR = "CODEPLAIN_NO_TELEMETRY"
ENVIRONMENT_ENV_VAR = "CODEPLAIN_ENV"
DEFAULT_ENVIRONMENT = "production"

FLUSH_TIMEOUT_SECONDS = 2

# Local variable names whose values may contain proprietary spec or generated
# code content and must be scrubbed from stack traces (extends Sentry's
# default denylist, which already covers api_key, auth, secrets etc.).
# "headers" and "x-api-key" cover the request headers local in
# codeplain_REST_api.post_request; the default denylist only has the
# underscore form "x_api_key".
SCRUB_DENYLIST = DEFAULT_DENYLIST + [
    "authorization",
    "headers",
    "x-api-key",
    "plain_source",
    "plain_source_tree",
    "full_plain_source",
    "existing_files_content",
    "file_content",
    "files_content",
    "content",
    "source",
    "response_json",
    "payload",
]


def telemetry_enabled() -> bool:
    """Return True if crash reporting should be active."""
    if os.environ.get(NO_TELEMETRY_ENV_VAR):
        return False
    return True


def initialize_telemetry(**init_overrides: Any) -> bool:
    """Initialize Sentry crash reporting. Returns True if initialized."""
    if not telemetry_enabled():
        return False

    try:
        init_kwargs: dict[str, Any] = dict(
            dsn=SENTRY_DSN,
            release=system_config.client_version,
            environment=os.environ.get(ENVIRONMENT_ENV_VAR, DEFAULT_ENVIRONMENT),
            send_default_pii=False,
            server_name="",  # hostname is identifying; don't send it
            default_integrations=False,
            auto_enabling_integrations=False,
            integrations=[
                AtexitIntegration(callback=lambda pending, timeout: None),
                DedupeIntegration(),
                ModulesIntegration(),
            ],
            include_local_variables=True,
            event_scrubber=EventScrubber(denylist=SCRUB_DENYLIST, recursive=True),
            shutdown_timeout=FLUSH_TIMEOUT_SECONDS,
        )
        init_kwargs.update(init_overrides)
        sentry_sdk.init(**init_kwargs)
        return True
    except Exception:
        return False


def capture_crash(exc_info, run_state: Optional[RunState], args) -> bool:
    """Report an unexpected crash to Sentry. Returns True if an event was sent."""
    if not telemetry_enabled():
        return False

    try:
        with sentry_sdk.new_scope() as scope:
            if run_state is not None:
                scope.set_tag("render_id", run_state.render_id)
                scope.set_tag("render_state", run_state.current_render_state)
                scope.set_tag("current_module", run_state.current_module)
                scope.set_tag("current_frid", run_state.current_frid)
            scope.set_tag("headless", bool(getattr(args, "headless", False)))
            scope.set_tag("unittests_script_provided", bool(getattr(args, "unittests_script", None)))
            scope.set_tag("conformance_tests_script_provided", bool(getattr(args, "conformance_tests_script", None)))
            scope.set_tag(
                "prepare_environment_script_provided", bool(getattr(args, "prepare_environment_script", None))
            )

            event_id = sentry_sdk.capture_exception(exc_info[1])

        sentry_sdk.flush(timeout=FLUSH_TIMEOUT_SECONDS)
        return event_id is not None
    except Exception:
        # Telemetry must never break the CLI or mask the original crash.
        return False
