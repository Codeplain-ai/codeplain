"""Characterization of `render_machine.render_utils.execute_script()`.

The behaviour asserted here is the behaviour of the pipe-based implementation as it
stands today: exit-code passthrough, stderr merged into stdout, the timeout result,
cancellation, and the pipe-buffer case the drain thread exists for. The PTY backend
that replaces the pipe path has to reproduce all of it.

Scripts are executed for real, so every case that runs one is POSIX-only; the Windows
branch of `execute_script()` accepts `.ps1` files only. `_sanitize_script_output()` is
platform-neutral and is exercised everywhere.
"""

import contextlib
import os
import shlex
import stat
import sys
import textwrap
import threading
from pathlib import Path

import pytest

from plain2code_exceptions import RenderCancelledError
from render_machine import render_utils

posix_only = pytest.mark.skipif(
    sys.platform == "win32",
    reason="execute_script() runs .ps1 scripts on Windows; these cases use shell scripts.",
)

SCRIPT_TYPE = "Characterization"

# Larger than the 64KB macOS pipe buffer, so the child blocks on write unless drained.
LARGE_OUTPUT_BYTES = 512 * 1024

CLEAR_SCREEN = "\033[2J"


def _make_shell_script(directory: Path, name: str, body: str) -> str:
    """Writes an executable /bin/sh script and returns its absolute path."""
    script_path = directory / f"{name}.sh"
    script_path.write_text("#!/bin/sh\n" + textwrap.dedent(body))
    script_path.chmod(script_path.stat().st_mode | stat.S_IXUSR)
    return str(script_path)


def _make_python_script(directory: Path, name: str, program: str) -> str:
    """Writes a shell wrapper around a Python program and returns the wrapper's path."""
    program_path = directory / f"{name}.py"
    program_path.write_text(textwrap.dedent(program))
    return _make_shell_script(
        directory,
        name,
        f'exec {shlex.quote(sys.executable)} {shlex.quote(str(program_path))} "$@"\n',
    )


@pytest.fixture
def run_script():
    """Calls execute_script() and removes the output files it leaves behind."""
    output_files = []

    def _run(*args, **kwargs):
        exit_code, output, output_file = render_utils.execute_script(*args, **kwargs)
        if output_file:
            output_files.append(output_file)
        return exit_code, output, output_file

    yield _run

    for output_file in output_files:
        with contextlib.suppress(OSError):
            os.remove(output_file)


@posix_only
def test_successful_script_returns_zero_with_its_output(tmp_path, run_script):
    script = _make_shell_script(tmp_path, "success", 'echo "ran with $1 $2"\n')

    exit_code, output, output_file = run_script(script, ["first", "second"], SCRIPT_TYPE, timeout=30)

    assert exit_code == 0
    assert "ran with first second" in output
    assert os.path.isfile(output_file)


@posix_only
@pytest.mark.parametrize("expected_exit_code", [1, 3, 69])
def test_failing_script_exit_code_is_returned_verbatim(tmp_path, run_script, expected_exit_code):
    script = _make_shell_script(
        tmp_path,
        f"exit_{expected_exit_code}",
        f'echo "failing"\nexit {expected_exit_code}\n',
    )

    exit_code, output, _ = run_script(script, [], SCRIPT_TYPE, timeout=30)

    assert exit_code == expected_exit_code
    assert "failing" in output


@posix_only
def test_stderr_is_merged_into_the_captured_output(tmp_path, run_script):
    script = _make_shell_script(
        tmp_path,
        "both_streams",
        'echo "on stdout"\necho "on stderr" >&2\n',
    )

    exit_code, output, _ = run_script(script, [], SCRIPT_TYPE, timeout=30)

    assert exit_code == 0
    assert "on stdout" in output
    assert "on stderr" in output


@posix_only
def test_output_larger_than_the_pipe_buffer_is_captured_without_deadlock(tmp_path, run_script):
    script = _make_python_script(
        tmp_path,
        "large_output",
        f"""
        import sys

        sys.stdout.write("x" * {LARGE_OUTPUT_BYTES})
        sys.stdout.write("\\nEND-OF-OUTPUT\\n")
        """,
    )

    exit_code, output, _ = run_script(script, [], SCRIPT_TYPE, timeout=60)

    assert exit_code == 0
    assert output.count("x") == LARGE_OUTPUT_BYTES
    assert output.rstrip().endswith("END-OF-OUTPUT")


@posix_only
def test_script_exceeding_the_timeout_returns_124_and_keeps_partial_output(tmp_path, run_script):
    script = _make_shell_script(
        tmp_path,
        "slow",
        'echo "printed before the timeout"\nsleep 30\n',
    )

    exit_code, output, output_file = run_script(script, [], SCRIPT_TYPE, timeout=2)

    assert exit_code == render_utils.TIMEOUT_ERROR_EXIT_CODE
    assert exit_code == 124
    assert "did not finish in 2 seconds" in output
    assert "printed before the timeout" in output
    assert "printed before the timeout" in Path(output_file).read_text()


@posix_only
def test_set_stop_event_cancels_the_script(tmp_path):
    script = _make_shell_script(tmp_path, "cancellable", "sleep 30\n")
    stop_event = threading.Event()
    stop_event.set()

    with pytest.raises(RenderCancelledError):
        render_utils.execute_script(script, [], SCRIPT_TYPE, timeout=30, stop_event=stop_event)


@posix_only
def test_script_without_a_path_is_resolved_against_the_working_directory(tmp_path, run_script, monkeypatch):
    _make_shell_script(tmp_path, "bare_name", 'echo "resolved from the working directory"\n')
    monkeypatch.chdir(tmp_path)

    exit_code, output, _ = run_script("bare_name.sh", [], SCRIPT_TYPE, timeout=30)

    assert exit_code == 0
    assert "resolved from the working directory" in output


@pytest.mark.parametrize(
    "script_output, expected",
    [
        ("plain output", "plain output"),
        ("", ""),
        (f"before{CLEAR_SCREEN}after", "after"),
        (f"first{CLEAR_SCREEN}second{CLEAR_SCREEN}third", "third"),
        (f"before\033[H{CLEAR_SCREEN}\033[3Jafter", "after"),
        (f"trailing{CLEAR_SCREEN}", ""),
        ("\033[31mred\033[0m", "\033[31mred\033[0m"),
    ],
)
def test_sanitize_script_output_keeps_only_what_follows_the_last_screen_clear(script_output, expected):
    assert render_utils._sanitize_script_output(script_output) == expected
