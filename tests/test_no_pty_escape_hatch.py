"""The `CODEPLAIN_NO_PTY` escape hatch.

An explicit user override, never an automatic fallback: a failed `openpty()` stays an
environment error, because a silent downgrade would make execution behaviour
machine-dependent again. What is asserted here is the contract that keeps it an override —
the exact value that selects it, the warning on every use, the variable's absence from the
child's environment — plus the characterization cases, re-run unchanged against the pipe
backend the hatch selects.
"""

import json
import sys

import pytest

from render_machine._legacy_pipe import LegacyPipeProcess
from render_machine.terminal_process import (
    ENVIRONMENT_ERROR_EXIT_CODE,
    NO_PTY_ENV_VAR,
    create_terminal_process,
    pty_disabled_by_environment,
)
from tests import test_render_utils as characterization

posix_only = pytest.mark.skipif(
    sys.platform == "win32",
    reason="These cases run POSIX shell and Python scripts directly.",
)

# The backend the platform selects when the hatch is closed. Both are reachable from every
# platform's suite, because the hatch is what decides, not the platform.
if sys.platform == "win32":
    from render_machine._conpty import ConPtyProcess as DefaultBackend
else:
    from render_machine._posix_pty import PosixPtyProcess as DefaultBackend

SCRIPT_TYPE = characterization.SCRIPT_TYPE


@pytest.fixture(autouse=True)
def hatch(monkeypatch):
    """Every case in this module runs with the hatch open."""
    monkeypatch.setenv(NO_PTY_ENV_VAR, "1")


@pytest.fixture
def hatch_warnings(monkeypatch):
    """Records the warnings that name the hatch, without printing any of them.

    `console` is one shared object, so the recorder sees every warning the execution
    emits; only the ones naming the variable belong to the hatch.
    """
    recorded = []
    monkeypatch.setattr(
        "render_machine.terminal_process.console.warning",
        lambda message: recorded.append(message) if NO_PTY_ENV_VAR in message else None,
    )
    return recorded


# The characterization cases, re-run unchanged. The terminal-isolation case is not among
# them: the pipe backend gives the script DEVNULL rather than a terminal of its own, and
# its own module asserts that the script still never reaches Codeplain's terminal.
run_script = characterization.run_script

test_successful_script_returns_zero_with_its_output = (
    characterization.test_successful_script_returns_zero_with_its_output
)
test_failing_script_exit_code_is_returned_verbatim = characterization.test_failing_script_exit_code_is_returned_verbatim
test_stderr_is_merged_into_the_captured_output = characterization.test_stderr_is_merged_into_the_captured_output
test_output_larger_than_the_pipe_buffer_is_captured_without_deadlock = (
    characterization.test_output_larger_than_the_pipe_buffer_is_captured_without_deadlock
)
test_script_exceeding_the_timeout_returns_124_and_keeps_partial_output = (
    characterization.test_script_exceeding_the_timeout_returns_124_and_keeps_partial_output
)
test_a_set_stop_event_cancels_the_script_without_ever_launching_it = (
    characterization.test_a_set_stop_event_cancels_the_script_without_ever_launching_it
)
test_script_without_a_path_is_resolved_against_the_working_directory = (
    characterization.test_script_without_a_path_is_resolved_against_the_working_directory
)
test_a_repainted_screen_yields_one_frame_and_no_escape_sequences = (
    characterization.test_a_repainted_screen_yields_one_frame_and_no_escape_sequences
)


def test_the_hatch_selects_the_pipe_backend(hatch_warnings):
    """The hatch is consulted before the platform, so it holds on Windows as well."""
    process = create_terminal_process()
    try:
        assert isinstance(process, LegacyPipeProcess)
    finally:
        process.close()


@pytest.mark.parametrize("value", ["", "0", "true", "yes", "11", " 1"])
def test_only_the_value_one_selects_the_pipe_backend(monkeypatch, value):
    monkeypatch.setenv(NO_PTY_ENV_VAR, value)

    assert pty_disabled_by_environment() is False
    process = create_terminal_process()
    try:
        assert isinstance(process, DefaultBackend)
    finally:
        process.close()


def test_the_warning_names_the_variable_on_every_use(hatch_warnings):
    for _ in range(2):
        create_terminal_process().close()

    assert len(hatch_warnings) == 2
    for message in hatch_warnings:
        assert NO_PTY_ENV_VAR in message
        assert "isatty" in message


def test_no_warning_is_emitted_when_the_hatch_is_closed(monkeypatch, hatch_warnings):
    monkeypatch.delenv(NO_PTY_ENV_VAR)

    create_terminal_process().close()

    assert hatch_warnings == []


ENVIRONMENT_PROBE_PROGRAM = f"""
import json
import os
import sys

sys.stdout.write(json.dumps({{"present": "{NO_PTY_ENV_VAR}" in os.environ}}))
sys.stdout.flush()
"""


@posix_only
@pytest.mark.parametrize("value", ["1", "0"])
def test_the_variable_never_reaches_the_child(tmp_path, run_script, monkeypatch, value):
    """Whichever backend it selects, a rendered script must not be able to branch on it."""
    monkeypatch.setenv(NO_PTY_ENV_VAR, value)
    script = characterization._make_python_script(tmp_path, "env_probe", ENVIRONMENT_PROBE_PROGRAM)

    exit_code, output, _ = run_script(script, [], SCRIPT_TYPE, timeout=30)

    assert exit_code == 0
    assert json.loads(output.strip()) == {"present": False}


@posix_only
def test_a_failed_openpty_is_an_environment_error_rather_than_a_downgrade(
    tmp_path, run_script, monkeypatch, hatch_warnings
):
    """The hatch is the only way to the pipe backend; PTY exhaustion is never a fallback."""
    monkeypatch.delenv(NO_PTY_ENV_VAR)
    script = characterization._make_shell_script(tmp_path, "never_runs", 'echo "unreachable"\n')

    def refuse_to_allocate():
        raise OSError(23, "too many open files in system")

    monkeypatch.setattr("render_machine._posix_pty.os.openpty", refuse_to_allocate)

    exit_code, issue, _ = run_script(script, [], SCRIPT_TYPE, timeout=30)

    assert exit_code == ENVIRONMENT_ERROR_EXIT_CODE
    assert "pseudoterminal" in issue
    assert "unreachable" not in issue
    assert hatch_warnings == []


@posix_only
def test_the_hatch_is_read_at_every_spawn(tmp_path, run_script, monkeypatch):
    """Read at spawn, not cached at import, so opening it takes effect immediately."""
    monkeypatch.delenv(NO_PTY_ENV_VAR)
    script = characterization._make_python_script(tmp_path, "isatty_probe", ISATTY_PROBE_PROGRAM)

    _, with_pty, _ = run_script(script, [], SCRIPT_TYPE, timeout=30)
    monkeypatch.setenv(NO_PTY_ENV_VAR, "1")
    _, without_pty, _ = run_script(script, [], SCRIPT_TYPE, timeout=30)

    assert json.loads(with_pty.strip()) == {"isatty": True}
    assert json.loads(without_pty.strip()) == {"isatty": False}


ISATTY_PROBE_PROGRAM = """
import json
import os
import sys

sys.stdout.write(json.dumps({"isatty": os.isatty(0) and os.isatty(1)}))
sys.stdout.flush()
"""
