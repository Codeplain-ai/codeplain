"""Tests for the legacy pipe backend behind `TerminalProcess`.

The backend wraps the pipe path Codeplain shipped before the PTY, so what is asserted here
is that path's behaviour — exit codes, merged streams, a drain that survives more output
than the pipe buffer holds — plus the properties the interface adds: no input channel, a
responder that owes nothing, and normalized output alongside the raw bytes.

Scripts are executed for real, so every case that runs one is POSIX-only.
"""

import contextlib
import errno
import json
import os
import stat
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest

from render_machine.terminal_process import READER_STALL_DETAIL, InputDisposition, TerminalReaderError
from render_machine.terminal_queries import ResponderState

posix_only = pytest.mark.skipif(
    sys.platform == "win32",
    reason="These cases run POSIX shell and Python scripts directly.",
)

pytestmark = posix_only

if sys.platform != "win32":
    from render_machine import _legacy_pipe
    from render_machine._legacy_pipe import LegacyPipeProcess

SPAWN_TIMEOUT = 20.0

# Larger than the 64KB macOS pipe buffer, so the child blocks on write unless drained.
LARGE_OUTPUT_BYTES = 512 * 1024


def make_script(directory: Path, name: str, program: str) -> str:
    """Writes an executable Python script and returns its absolute path."""
    script_path = directory / f"{name}.py"
    script_path.write_text(f"#!{sys.executable}\n" + textwrap.dedent(program))
    script_path.chmod(script_path.stat().st_mode | stat.S_IXUSR)
    return str(script_path)


def make_shell_script(directory: Path, name: str, body: str) -> str:
    script_path = directory / f"{name}.sh"
    script_path.write_text("#!/bin/sh\n" + textwrap.dedent(body))
    script_path.chmod(script_path.stat().st_mode | stat.S_IXUSR)
    return str(script_path)


@pytest.fixture
def backend():
    process = LegacyPipeProcess()
    try:
        yield process
    finally:
        process.terminate_tree(grace=0.1)
        process.close()


def wait_for_exit(process, timeout=SPAWN_TIMEOUT):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        returncode = process.poll()
        if returncode is not None:
            return returncode
        time.sleep(0.02)
    raise AssertionError(f"the target did not exit within {timeout}s")


def run(process, command, timeout=SPAWN_TIMEOUT):
    """Spawns, waits for the exit, and closes so every byte has been drained."""
    process.spawn(command)
    returncode = wait_for_exit(process, timeout)
    process.close()
    return returncode


def test_exit_code_and_merged_streams_reach_the_caller(tmp_path, backend):
    script = make_shell_script(tmp_path, "both_streams", 'echo "on stdout"\necho "on stderr" >&2\nexit 3\n')

    returncode = run(backend, [script])

    output = backend.read_output()
    assert returncode == 3
    assert "on stdout" in output
    assert "on stderr" in output


def test_output_larger_than_the_pipe_buffer_is_captured_without_deadlock(tmp_path, backend):
    script = make_script(
        tmp_path,
        "large_output",
        f"""
        import sys

        sys.stdout.write("x" * {LARGE_OUTPUT_BYTES})
        sys.stdout.write("\\nEND-OF-OUTPUT\\n")
        """,
    )

    returncode = run(backend, [script], timeout=60)

    output = backend.read_output()
    assert returncode == 0
    assert output.count("x") == LARGE_OUTPUT_BYTES
    assert output.rstrip().endswith("END-OF-OUTPUT")


def test_raw_bytes_are_kept_verbatim_and_the_transcript_is_normalized(tmp_path, backend):
    script = make_script(
        tmp_path,
        "coloured",
        """
        import sys

        sys.stdout.write("\\033[31mred\\033[0m\\n")
        """,
    )

    assert run(backend, [script]) == 0

    assert b"\033[31m" in backend.read_raw_output()
    assert backend.normalized_output() == "red\n"


def test_a_printed_query_is_rendered_without_creating_an_obligation(tmp_path, backend):
    script = make_script(
        tmp_path,
        "querying",
        """
        import sys

        sys.stdout.write("\\033[6nbefore\\033[5nafter\\n")
        """,
    )

    assert run(backend, [script]) == 0

    assert backend.query_responder.state is ResponderState.QUIESCED
    assert backend.query_responder.render_only >= 2
    assert backend.query_responder.admitted == 0
    assert backend.terminal_reply_failed is False
    assert backend.terminal_reply_detail() == ""


def test_write_input_accepts_nothing(tmp_path, backend):
    script = make_shell_script(tmp_path, "quiet", "sleep 30\n")
    backend.spawn([script])

    result = backend.write_input(b"anything\n")

    assert result.disposition is InputDisposition.CLOSED
    assert result.accepted_bytes == 0


def test_terminate_tree_reaches_a_descendant(tmp_path, backend):
    script = make_script(
        tmp_path,
        "with_descendant",
        """
        import os
        import sys
        import time

        pid = os.fork()
        if pid == 0:
            time.sleep(300)
            os._exit(0)
        sys.stdout.write("child %d\\n" % pid)
        sys.stdout.flush()
        time.sleep(300)
        """,
    )
    backend.spawn([script])

    deadline = time.monotonic() + SPAWN_TIMEOUT
    reported = ""
    while time.monotonic() < deadline and "\n" not in reported:
        reported += backend.read_output()
        time.sleep(0.02)
    assert "\n" in reported
    descendant_pid = int(reported.split()[1])

    backend.terminate_tree(grace=0.5)

    gone_by = time.monotonic() + SPAWN_TIMEOUT
    while time.monotonic() < gone_by:
        try:
            os.kill(descendant_pid, 0)
        except OSError:
            return
        time.sleep(0.02)
    raise AssertionError("the descendant outlived terminate_tree()")


def test_close_is_idempotent_and_survives_a_process_that_never_spawned(backend):
    backend.close()
    backend.close()

    assert backend.poll() is None
    assert backend.read_output() == ""


def test_instances_are_single_use(tmp_path, backend):
    script = make_shell_script(tmp_path, "trivial", "true\n")
    backend.spawn([script])

    with pytest.raises(RuntimeError):
        backend.spawn([script])


def test_a_command_that_cannot_be_started_is_an_environment_error(tmp_path, backend):
    from render_machine.terminal_process import ENVIRONMENT_ERROR_EXIT_CODE, TerminalLaunchError

    missing = str(tmp_path / "not-a-real-script")

    with pytest.raises(TerminalLaunchError) as failure:
        backend.spawn([missing])

    assert failure.value.exit_code == ENVIRONMENT_ERROR_EXIT_CODE


def test_a_read_failure_while_the_backend_is_active_is_published(tmp_path, backend):
    """An OSError from the read path is expected closure only once the pipe is gone."""
    script = make_shell_script(tmp_path, "chatty", "while true; do printf tick; sleep 0.05; done\n")

    def failing_feed(chunk, decoder):
        raise OSError(errno.EIO, "injected reader failure")

    backend._feed_output = failing_feed
    backend.spawn([script])

    deadline = time.monotonic() + SPAWN_TIMEOUT
    while not backend.reader_failed.is_set() and time.monotonic() < deadline:
        time.sleep(0.02)

    assert backend.reader_failed.is_set()
    assert isinstance(backend.reader_exc, OSError)


def test_a_reader_that_outlives_its_join_bound_is_published_as_a_reader_failure(tmp_path, backend, monkeypatch):
    """close() must not report a released backend while the reader still holds the pipe."""
    monkeypatch.setattr(_legacy_pipe, "DRAIN_DEADLINE_SECONDS", 0.05)
    monkeypatch.setattr(_legacy_pipe, "CLOSE_JOIN_SECONDS", 0.05)
    script = make_shell_script(tmp_path, "prints_then_waits", 'echo "hello"\nsleep 30\n')
    reading = threading.Event()
    release = threading.Event()
    real_feed = backend._feed_output

    def stalling_feed(chunk, decoder):
        reading.set()
        release.wait(SPAWN_TIMEOUT)  # holds the reader past both joins in close()
        real_feed(chunk, decoder)

    backend._feed_output = stalling_feed
    backend.spawn([script])
    assert reading.wait(SPAWN_TIMEOUT)

    try:
        with pytest.raises(TerminalReaderError) as failure:
            backend.close()
        assert READER_STALL_DETAIL in str(failure.value)
        assert backend.reader_failed.is_set()
    finally:
        release.set()


# --- The terminal-isolation guard ------------------------------------------------
#
# The escape hatch and the Windows interim both run on this backend, so it has to keep
# the child away from Codeplain's own terminal exactly as the PTY path does.

KEYSTROKES = "secret-keystrokes\n"
STDIN_READ_LIMIT = 1024
IMMEDIATE_EOF_SECONDS = 5

STDIN_PROBE_PROGRAM = f"""
import json
import os
import sys
import time

started = time.monotonic()
data = os.read(0, {STDIN_READ_LIMIT})
report = {{
    "isatty": os.isatty(0),
    "data": data.decode(errors="replace"),
    "read_seconds": time.monotonic() - started,
}}
sys.stdout.write(json.dumps(report))
sys.stdout.flush()
"""


@pytest.fixture
def terminal_on_stdin():
    """Puts a PTY slave on the test process's fd 0 and yields the master fd."""
    try:
        saved_stdin_fd = os.dup(0)
    except OSError as exc:
        pytest.skip(f"fd 0 cannot be duplicated in this environment: {exc}")

    master_fd, slave_fd = os.openpty()
    os.dup2(slave_fd, 0)
    try:
        yield master_fd
    finally:
        os.dup2(saved_stdin_fd, 0)
        for fd in (saved_stdin_fd, slave_fd, master_fd):
            with contextlib.suppress(OSError):
                os.close(fd)


def test_the_child_never_reads_the_renderers_terminal(tmp_path, backend, terminal_on_stdin):
    script = make_script(tmp_path, "stdin_probe", STDIN_PROBE_PROGRAM)
    os.write(terminal_on_stdin, KEYSTROKES.encode())

    started = time.monotonic()
    assert run(backend, [script]) == 0
    elapsed = time.monotonic() - started

    report = json.loads(backend.read_output().strip())
    assert report["isatty"] is False
    assert report["data"] == ""
    assert report["read_seconds"] < IMMEDIATE_EOF_SECONDS
    assert elapsed < SPAWN_TIMEOUT


def test_the_control_case_proves_the_harness_terminal_delivers_keystrokes(tmp_path, terminal_on_stdin):
    """Without this the isolation assertion above could hold for the wrong reason."""
    script = make_script(tmp_path, "inheriting_probe", STDIN_PROBE_PROGRAM)
    os.write(terminal_on_stdin, KEYSTROKES.encode())

    process = subprocess.Popen(
        [script],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )
    try:
        output, _ = process.communicate(timeout=SPAWN_TIMEOUT)
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate(timeout=SPAWN_TIMEOUT)
        pytest.fail("the control probe never returned from its read of fd 0")

    report = json.loads(output.strip())
    assert report["isatty"] is True
    assert report["data"] == KEYSTROKES
