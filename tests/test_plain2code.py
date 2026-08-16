import tempfile
import threading
import time
from argparse import Namespace
from types import SimpleNamespace
from unittest.mock import patch

import plain2code
import plain_spec
from plain_modules import PlainModule
from render_machine import _legacy_pipe, terminal_process


def _make_module(module_name, has_acceptance_tests, required_modules=None):
    """Build a minimal stand-in for a PlainModule.

    The functional requirements only need an `acceptance_tests` key when the module
    is expected to contain acceptance tests, because that is all
    `plain_spec.has_acceptance_tests` inspects.
    """
    functional_requirement = {"markdown": f"- {module_name} functionality."}
    if has_acceptance_tests:
        functional_requirement[plain_spec.ACCEPTANCE_TESTS] = [{"markdown": "- Test it."}]

    return SimpleNamespace(
        module_name=module_name,
        plain_source={plain_spec.FUNCTIONAL_REQUIREMENTS: [functional_requirement]},
        all_required_modules=required_modules or [],
    )


def test_warns_when_acceptance_tests_present_and_no_conformance_script():
    plain_module = _make_module("top", has_acceptance_tests=True)
    args = Namespace(conformance_tests_script=None)

    with patch("plain2code.console") as mock_console:
        plain2code.warn_if_acceptance_tests_without_conformance_script(plain_module, args)

    mock_console.warning.assert_called_once()
    warning_message = mock_console.warning.call_args.args[0]
    assert "top" in warning_message
    assert "conformance tests script" in warning_message


def test_no_warning_when_conformance_script_configured():
    plain_module = _make_module("top", has_acceptance_tests=True)
    args = Namespace(conformance_tests_script="run_conformance_tests.sh")

    with patch("plain2code.console") as mock_console:
        plain2code.warn_if_acceptance_tests_without_conformance_script(plain_module, args)

    mock_console.warning.assert_not_called()


def test_no_warning_when_no_acceptance_tests():
    plain_module = _make_module("top", has_acceptance_tests=False)
    args = Namespace(conformance_tests_script=None)

    with patch("plain2code.console") as mock_console:
        plain2code.warn_if_acceptance_tests_without_conformance_script(plain_module, args)

    mock_console.warning.assert_not_called()


def test_warns_when_acceptance_tests_only_in_required_module():
    required_module = _make_module("dependency", has_acceptance_tests=True)
    plain_module = _make_module("top", has_acceptance_tests=False, required_modules=[required_module])
    args = Namespace(conformance_tests_script=None)

    with patch("plain2code.console") as mock_console:
        plain2code.warn_if_acceptance_tests_without_conformance_script(plain_module, args)

    mock_console.warning.assert_called_once()
    warning_message = mock_console.warning.call_args.args[0]
    assert "dependency" in warning_message


def test_warning_covers_required_modules_for_real_plain_module(get_test_data_path):
    """Integration test: a main module without acceptance tests that requires a
    module with acceptance tests should still trigger the warning, naming the
    required module. This mirrors the dry-run path which builds a real PlainModule."""
    fixtures_dir = get_test_data_path("data/acceptance_tests_warning")
    with tempfile.TemporaryDirectory() as build:
        plain_module = PlainModule(
            "main_requiring_acceptance_tests.plain",
            build,
            [fixtures_dir],
        )

    args = Namespace(conformance_tests_script=None)
    with patch("plain2code.console") as mock_console:
        plain2code.warn_if_acceptance_tests_without_conformance_script(plain_module, args)

    mock_console.warning.assert_called_once()
    warning_message = mock_console.warning.call_args.args[0]
    assert "required_with_acceptance_tests" in warning_message


# --- The shutdown wait after the TUI closes --------------------------------------
#
# The render runs on a daemon thread, so whatever it is still doing when the wrapper
# returns is abandoned. What it is usually still doing is tearing a script down on the
# backend's clock, which is why the wait has to outlast that clock rather than a fixed
# fraction of it.

# What the wrapper waited before this was fixed — shorter than the SIGTERM grace a script
# teardown runs to its end, so the CLI could exit mid-escalation.
SUPERSEDED_SHUTDOWN_TIMEOUT = 0.7
SLOW_TEARDOWN_SECONDS = SUPERSEDED_SHUTDOWN_TIMEOUT + 0.3

# The wedged thread is released by the test, not by the timeout it would otherwise sit on.
WEDGED_THREAD_TIMEOUT = 30.0
WEDGE_SHUTDOWN_TIMEOUT = 0.3
WEDGE_WAIT_CEILING = 5.0


def test_the_shutdown_bound_covers_the_teardown_budgets_of_a_script():
    """The wait is derived from what a backend teardown may spend, not picked.

    The budget comes from the backends themselves, so the wait covers whichever one this
    platform can reach rather than the phases of the POSIX pipeline alone.
    """
    budget = terminal_process.teardown_budget_seconds()
    posix_pipeline = (
        terminal_process.SIGTERM_GRACE_PERIOD_SECONDS
        + terminal_process.REAP_DEADLINE_SECONDS
        + terminal_process.DRAIN_DEADLINE_SECONDS
        + terminal_process.REAP_DEADLINE_SECONDS
    )

    assert budget >= posix_pipeline
    assert budget >= _legacy_pipe.TEARDOWN_BUDGET_SECONDS
    assert plain2code.RENDER_THREAD_SHUTDOWN_TIMEOUT > budget
    assert plain2code.RENDER_THREAD_SHUTDOWN_TIMEOUT > SUPERSEDED_SHUTDOWN_TIMEOUT


def test_shutdown_waits_for_a_teardown_that_outlasts_the_superseded_bound():
    stop_event = threading.Event()
    torn_down = threading.Event()

    def render_then_tear_down():
        stop_event.wait(timeout=WEDGED_THREAD_TIMEOUT)
        time.sleep(SLOW_TEARDOWN_SECONDS)  # the grace the backend runs to its end
        torn_down.set()

    render_thread = threading.Thread(target=render_then_tear_down, daemon=True)
    render_thread.start()

    with patch("plain2code.console"):
        completed = plain2code.shutdown_render_thread(render_thread, stop_event)

    assert completed is True
    assert torn_down.is_set()
    assert not render_thread.is_alive()


def test_shutdown_stays_bounded_when_the_teardown_never_completes(monkeypatch):
    """A teardown past the derived ceiling is reported, never waited on indefinitely."""
    monkeypatch.setattr(plain2code, "RENDER_THREAD_SHUTDOWN_TIMEOUT", WEDGE_SHUTDOWN_TIMEOUT)
    release = threading.Event()
    render_thread = threading.Thread(target=lambda: release.wait(WEDGED_THREAD_TIMEOUT), daemon=True)
    render_thread.start()

    try:
        started = time.monotonic()
        with patch("plain2code.console") as mock_console:
            completed = plain2code.shutdown_render_thread(render_thread, threading.Event())
        elapsed = time.monotonic() - started

        assert completed is False
        assert elapsed < WEDGE_WAIT_CEILING
        mock_console.warning.assert_called_once()
    finally:
        release.set()
        render_thread.join(timeout=WEDGED_THREAD_TIMEOUT)
