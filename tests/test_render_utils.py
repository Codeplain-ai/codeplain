"""Characterization of `render_machine.render_utils.execute_script()`.

The behaviour asserted here is the contract the callers depend on: exit-code passthrough,
stderr merged into stdout, the timeout result with its partial output, cancellation, and
output that outruns the buffer without deadlocking. It was written against the pipe
implementation and now runs against the terminal backend, which has to reproduce all of
it. Two things legitimately changed with the backend: the transcript is rendered rather
than concatenated, so it is bounded by the retained scrollback, and the script's
descriptors are a terminal, so `isatty()` is true — while the terminal it gets is still
never Codeplain's own.

The outcome arbiter is exercised separately, against an injected backend, because the
conditions it ranks race with each other and cannot be provoked reliably from a script.

Scripts are executed for real, so every case that runs one is POSIX-only; the Windows
branch of `execute_script()` accepts `.ps1` files only.
"""

import contextlib
import json
import os
import stat
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
from pathlib import Path

import pytest

from plain2code_exceptions import RenderCancelledError
from render_machine import render_utils
from render_machine.terminal_process import (
    ENVIRONMENT_ERROR_EXIT_CODE,
    READER_STALL_DETAIL,
    TerminalLaunchError,
    TerminalProcess,
    TerminalProcessError,
)

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
    """Writes an executable Python script and returns its absolute path.

    The interpreter is named in the shebang rather than wrapped in a shell, so no shell
    ever sits between the caller and the program. A shell that inherits a terminal with
    pending input wedges on exit on macOS, which the terminal-isolation cases below rely
    on not happening.
    """
    script_path = directory / f"{name}.py"
    script_path.write_text(f"#!{sys.executable}\n" + textwrap.dedent(program))
    script_path.chmod(script_path.stat().st_mode | stat.S_IXUSR)
    return str(script_path)


RAW_ARTIFACT_GLOB = f"*.script_*{render_utils.RAW_OUTPUT_SUFFIX}"


@pytest.fixture(autouse=True)
def no_orphaned_raw_transcripts():
    """Every raw transcript must be reachable from the path execute_script() returned.

    Runs around every case in this module, including the ones that never call the
    `run_script` fixture, so an artifact nobody can name shows up as a failure here.
    """
    temp_dir = Path(tempfile.gettempdir())
    before = set(temp_dir.glob(RAW_ARTIFACT_GLOB))

    yield

    orphaned = set(temp_dir.glob(RAW_ARTIFACT_GLOB)) - before
    assert not orphaned, f"raw transcripts left behind: {sorted(str(path) for path in orphaned)}"


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
        # The raw transcript is a derived sibling, so the returned path names both.
        for path in (output_file, output_file + render_utils.RAW_OUTPUT_SUFFIX):
            with contextlib.suppress(OSError):
                os.remove(path)


@posix_only
def test_successful_script_returns_zero_with_its_output(tmp_path, run_script):
    script = _make_shell_script(tmp_path, "success", 'echo "ran with $1 $2"\n')

    exit_code, output, output_file = run_script(script, ["first", "second"], SCRIPT_TYPE, timeout=30)

    assert exit_code == 0
    assert "ran with first second" in output
    assert os.path.isfile(output_file)


@posix_only
def test_the_raw_transcript_is_a_named_sibling_of_the_published_output(tmp_path, run_script):
    """The unrendered bytes are findable from the returned path, so they can be cleaned up."""
    script = _make_shell_script(tmp_path, "coloured", 'printf "\\033[31mred\\033[0m\\n"\n')

    exit_code, _, output_file = run_script(script, [], SCRIPT_TYPE, timeout=30)

    raw_file = Path(output_file + render_utils.RAW_OUTPUT_SUFFIX)
    assert exit_code == 0
    assert b"\033[31m" in raw_file.read_bytes()


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
    # The transcript is rendered from the screen, so it keeps the retained scrollback
    # rather than every byte — but the run completes and its last line survives, which is
    # what the drain exists to guarantee.
    assert output.count("x") > 100_000
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
def test_a_set_stop_event_cancels_the_script_without_ever_launching_it(tmp_path, monkeypatch):
    """Cancellation is not a race the target gets to win: it never starts.

    A backend that launches first and notices the event afterwards has already let the
    script run whatever side effects it opens with. Whether the sentinel survives that
    depends on which of the two wins the microseconds, so the launch itself is what is
    asserted: both backends reach the target through `subprocess.Popen`, so a call that
    never happens is the proof, and the sentinel is the visible consequence.
    """
    sentinel = tmp_path / "the-target-ran"
    script = _make_shell_script(tmp_path, "cancellable", f'touch "{sentinel}"\nsleep 30\n')
    launched = []

    def refuse_to_launch(*args, **kwargs):
        launched.append(args[0] if args else kwargs.get("args"))
        raise AssertionError("the target was launched after cancellation had already been observed")

    monkeypatch.setattr(subprocess, "Popen", refuse_to_launch)
    stop_event = threading.Event()
    stop_event.set()
    cancelled = False

    try:
        render_utils.execute_script(script, [], SCRIPT_TYPE, timeout=30, stop_event=stop_event)
    except RenderCancelledError:
        cancelled = True

    assert not launched
    assert not sentinel.exists()
    assert cancelled


@posix_only
def test_script_without_a_path_is_resolved_against_the_working_directory(tmp_path, run_script, monkeypatch):
    _make_shell_script(tmp_path, "bare_name", 'echo "resolved from the working directory"\n')
    monkeypatch.chdir(tmp_path)

    exit_code, output, _ = run_script("bare_name.sh", [], SCRIPT_TYPE, timeout=30)

    assert exit_code == 0
    assert "resolved from the working directory" in output


@posix_only
def test_a_repainted_screen_yields_one_frame_and_no_escape_sequences(tmp_path, run_script):
    """What the screen-clear sanitizer used to approximate, now done by rendering it."""
    script = _make_python_script(
        tmp_path,
        "repainting",
        f"""
        import sys

        for frame in range(3):
            sys.stdout.write("{CLEAR_SCREEN}\\033[H")
            sys.stdout.write("\\033[32mframe %d\\033[0m\\n" % frame)
        sys.stdout.flush()
        """,
    )

    exit_code, output, _ = run_script(script, [], SCRIPT_TYPE, timeout=30)

    assert exit_code == 0
    assert output == "frame 2\n"
    assert "\033[" not in output


# --- The terminal-isolation guard ------------------------------------------------
#
# A rendered script must never be able to read the terminal Codeplain itself is
# attached to. The harness therefore has to hold a real terminal: it puts a PTY slave
# on its own fd 0 and writes to the master, which is what a user typing into the TUI
# does. Without that, pytest's fd 0 is not a terminal and the assertion would hold for
# the wrong reason.

KEYSTROKES = "secret-keystrokes\n"
CONTROL_PROBE_TIMEOUT_SECONDS = 20
STDIN_READ_LIMIT = 1024
IMMEDIATE_EOF_SECONDS = 5

# Reports what fd 0 is and what a read of it yields.
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


def _probe_report(output):
    return json.loads(output.strip())


@posix_only
def test_terminal_bytes_reach_a_child_that_inherits_stdin(tmp_path, terminal_on_stdin):
    """Control case: proves the harness's terminal really does deliver keystrokes."""
    script = _make_python_script(tmp_path, "inheriting_probe", STDIN_PROBE_PROGRAM)
    os.write(terminal_on_stdin, KEYSTROKES.encode())

    # The spawn shape execute_script() uses, minus the stdin redirection under test.
    process = subprocess.Popen(
        [script],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )
    try:
        output, _ = process.communicate(timeout=CONTROL_PROBE_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate(timeout=CONTROL_PROBE_TIMEOUT_SECONDS)
        pytest.fail("the control probe never returned from its read of fd 0")

    report = _probe_report(output)
    assert report["isatty"] is True
    assert report["data"] == KEYSTROKES


@posix_only
def test_script_stdin_is_a_terminal_of_its_own_and_never_the_renderers(tmp_path, run_script, terminal_on_stdin):
    """The script gets a terminal — just not this one, and with nothing queued on it."""
    script = _make_python_script(tmp_path, "stdin_probe", STDIN_PROBE_PROGRAM)
    os.write(terminal_on_stdin, KEYSTROKES.encode())

    started = time.monotonic()
    exit_code, output, _ = run_script(script, [], SCRIPT_TYPE, timeout=30)
    elapsed = time.monotonic() - started

    assert exit_code == 0
    report = _probe_report(output)
    assert report["isatty"] is True
    assert report["data"] == ""  # the spawn-time VEOF, never the keystrokes above
    assert KEYSTROKES.strip() not in output
    assert report["read_seconds"] < IMMEDIATE_EOF_SECONDS
    assert elapsed < CONTROL_PROBE_TIMEOUT_SECONDS


# --- The outcome arbiter ---------------------------------------------------------
#
# Every condition below can be observed while another is already being cleaned up, so
# the cases are driven through an injected backend rather than through a real script:
# the point is which condition wins, not how it arose.

# execute_script() accepts only .ps1 on Windows, and that check runs before the
# injected backend is reached, so the fake name has to match the platform.
FAKE_SCRIPT = "arbiter.ps1" if sys.platform == "win32" else "arbiter.sh"
FAKE_OUTPUT = "fake transcript\n"
READER_FAILURE = RuntimeError("the master descriptor went away")
REPLY_DETAIL = "cursor-position reply discarded before delivery"


class _FakeTerminalProcess(TerminalProcess):
    """A backend whose outcome is scripted, including failures discovered during teardown.

    Its reader is modelled rather than assumed: `reader_running` stays true until a
    `close()` that actually joined it, so a case can leave a reader alive past the join
    bound and the barrier has something real to hold.
    """

    def __init__(
        self,
        exit_code=None,
        spawn_error=None,
        poll_error=None,
        reader_fails_while_running=False,
        reader_fails_on_close=False,
        reader_outlives_close=False,
        teardown_error=None,
        reply_failed=False,
    ):
        self.reader_failed = threading.Event()
        self.reader_exc = None
        self.exit_code = exit_code
        self.spawn_error = spawn_error
        self.poll_error = poll_error
        self.reader_fails_while_running = reader_fails_while_running
        self.reader_fails_on_close = reader_fails_on_close
        self.reader_outlives_close = reader_outlives_close
        self.teardown_error = teardown_error
        self._reply_failed = reply_failed
        self.terminated = False
        self.closed = False
        self.reader_running = True

    def spawn(self, command, cwd=None, env=None, terminal_size=(80, 24), stop_event=None, input_driver=None):
        if self.spawn_error is not None:
            raise self.spawn_error

    def poll(self):
        if self.poll_error is not None:
            raise self.poll_error
        if self.reader_fails_while_running:  # an independent failure, while the target runs
            self.reader_exc = READER_FAILURE
            self.reader_failed.set()
        return self.exit_code

    def read_output(self):
        return FAKE_OUTPUT

    def read_raw_output(self):
        return FAKE_OUTPUT.encode()

    def normalized_output(self):
        return FAKE_OUTPUT

    @property
    def terminal_reply_failed(self):
        return self._reply_failed

    def terminal_reply_detail(self):
        return REPLY_DETAIL if self._reply_failed else ""

    def write_input(self, data):
        raise AssertionError("the arbiter cases never write input")

    def terminate_tree(self, grace=0.0):
        self.terminated = True
        if self.reader_fails_on_close:  # discovered while the grace period runs
            self.reader_exc = READER_FAILURE
            self.reader_failed.set()
        if self.teardown_error is not None:
            raise self.teardown_error

    def close(self):
        self.closed = True
        if self.reader_outlives_close:
            self._publish_reader_stall()  # the join bound expired with the reader alive
        self.reader_running = False


@pytest.fixture
def injected_backend(monkeypatch):
    """Installs a scripted backend at the single construction site."""
    installed = {}

    def _install(**kwargs):
        process = _FakeTerminalProcess(**kwargs)
        installed["process"] = process
        monkeypatch.setattr(render_utils, "create_terminal_process", lambda: process)
        return process

    yield _install


RAISES_CANCELLED = "raises RenderCancelledError"

# Every case is decided within a single poll: a zero timeout makes the deadline already
# expired when it is first read, and the stop event is set before the call. Nothing waits
# on the clock, so the racing pairs below are as deterministic as the single conditions.
ARBITER_CASES = [
    # name, backend kwargs, stop_event set, timeout, expected exit code
    ("the deadline alone", {}, False, 0, render_utils.TIMEOUT_ERROR_EXIT_CODE),
    ("the deadline with a reader failure", {"reader_fails_on_close": True}, False, 0, ENVIRONMENT_ERROR_EXIT_CODE),
    ("the deadline with an exit observed in the same poll", {"exit_code": 3}, False, 0, 124),
    ("a cancellation alone", {}, True, 30, RAISES_CANCELLED),
    ("a cancellation with a query failure", {"reply_failed": True}, True, 30, RAISES_CANCELLED),
    ("a cancellation with a reader failure", {"reader_fails_on_close": True}, True, 30, ENVIRONMENT_ERROR_EXIT_CODE),
    ("a cancellation with the deadline expired", {}, True, 0, RAISES_CANCELLED),
    ("a cancellation with an exit observed in the same poll", {"exit_code": 3}, True, 30, RAISES_CANCELLED),
    ("a nonzero exit alone", {"exit_code": 3}, False, 30, 3),
    (
        "a nonzero exit with a query failure",
        {"exit_code": 3, "reply_failed": True},
        False,
        30,
        ENVIRONMENT_ERROR_EXIT_CODE,
    ),
    (
        "a zero exit with a query failure",
        {"exit_code": 0, "reply_failed": True},
        False,
        30,
        ENVIRONMENT_ERROR_EXIT_CODE,
    ),
    (
        "a launch failure",
        {"spawn_error": TerminalLaunchError("openpty failed")},
        False,
        30,
        ENVIRONMENT_ERROR_EXIT_CODE,
    ),
]


@pytest.mark.parametrize(
    "case_name, backend_kwargs, cancelled, timeout, expected",
    ARBITER_CASES,
    ids=[case[0] for case in ARBITER_CASES],
)
def test_the_arbiter_ranks_every_condition_that_can_race(
    case_name, backend_kwargs, cancelled, timeout, expected, injected_backend, run_script
):
    process = injected_backend(**backend_kwargs)
    stop_event = threading.Event()
    if cancelled:
        stop_event.set()

    if expected is RAISES_CANCELLED:
        with pytest.raises(RenderCancelledError):
            render_utils.execute_script(FAKE_SCRIPT, [], SCRIPT_TYPE, timeout=timeout, stop_event=stop_event)
    else:
        exit_code, _, _ = run_script(FAKE_SCRIPT, [], SCRIPT_TYPE, timeout=timeout, stop_event=stop_event)
        assert exit_code == expected

    # Teardown runs before publication on every path, and it joined the reader: nothing
    # can append to the transcript or publish a failure after the outcome was decided.
    assert process.closed
    assert process.reader_running is False


def test_a_reader_failure_during_teardown_names_the_reader(injected_backend, run_script):
    injected_backend(exit_code=0, reader_fails_on_close=True)

    exit_code, issue, _ = run_script(FAKE_SCRIPT, [], SCRIPT_TYPE, timeout=30)

    assert exit_code == ENVIRONMENT_ERROR_EXIT_CODE
    assert "reader" in issue


def test_an_undeliverable_reply_names_the_query_that_went_unanswered(injected_backend, run_script):
    injected_backend(exit_code=0, reply_failed=True)

    exit_code, issue, _ = run_script(FAKE_SCRIPT, [], SCRIPT_TYPE, timeout=30)

    assert exit_code == ENVIRONMENT_ERROR_EXIT_CODE
    assert REPLY_DETAIL in issue


def test_a_reader_failure_while_the_target_runs_is_an_environment_error(injected_backend, run_script):
    """An active reader that dies is infrastructure, not the exit status it coincides with."""
    injected_backend(exit_code=0, reader_fails_while_running=True)

    exit_code, issue, _ = run_script(FAKE_SCRIPT, [], SCRIPT_TYPE, timeout=30)

    assert exit_code == ENVIRONMENT_ERROR_EXIT_CODE
    assert "reader" in issue


def test_a_reader_that_outlives_the_join_bound_is_an_environment_error(injected_backend, run_script):
    """close() cannot report a released backend while its reader is still running."""
    process = injected_backend(exit_code=0, reader_outlives_close=True)

    exit_code, issue, _ = run_script(FAKE_SCRIPT, [], SCRIPT_TYPE, timeout=30)

    assert exit_code == ENVIRONMENT_ERROR_EXIT_CODE
    assert READER_STALL_DETAIL in issue
    assert process.reader_running is True  # exactly the state the barrier has to catch


def test_a_backend_that_raises_something_unforeseen_on_spawn_still_returns_69(injected_backend, run_script):
    """Thread.start() failing is a RuntimeError, and must not escape the tuple contract."""
    injected_backend(spawn_error=RuntimeError("can't start new thread"))

    exit_code, issue, output_file = run_script(FAKE_SCRIPT, [], SCRIPT_TYPE, timeout=30)

    assert exit_code == ENVIRONMENT_ERROR_EXIT_CODE
    assert "can't start new thread" in issue
    assert os.path.isfile(output_file)


def test_a_backend_that_raises_while_polling_still_returns_69(injected_backend, run_script):
    injected_backend(poll_error=OSError("the child could not be waited on"))

    exit_code, issue, _ = run_script(FAKE_SCRIPT, [], SCRIPT_TYPE, timeout=30)

    assert exit_code == ENVIRONMENT_ERROR_EXIT_CODE
    assert "the child could not be waited on" in issue


def test_a_teardown_failure_does_not_displace_the_launch_failure_it_followed(injected_backend, run_script):
    injected_backend(
        spawn_error=TerminalLaunchError("openpty failed"),
        teardown_error=TerminalProcessError("the process group could not be signalled"),
    )

    exit_code, issue, _ = run_script(FAKE_SCRIPT, [], SCRIPT_TYPE, timeout=30)

    assert exit_code == ENVIRONMENT_ERROR_EXIT_CODE
    assert "could not be executed: openpty failed" in issue  # the launch failure leads
    assert issue.index("openpty failed") < issue.index("could not be signalled")


def test_a_launch_failure_is_reported_on_the_environment_channel_and_never_as_127(injected_backend, run_script):
    injected_backend(spawn_error=TerminalLaunchError("the launcher hung before exec"))

    exit_code, issue, output_file = run_script(FAKE_SCRIPT, [], SCRIPT_TYPE, timeout=30)

    assert exit_code == ENVIRONMENT_ERROR_EXIT_CODE
    assert exit_code != 127
    assert "the launcher hung before exec" in issue
    assert os.path.isfile(output_file)


@posix_only
def test_a_script_that_cannot_be_executed_is_an_environment_error(tmp_path, run_script):
    """The real path: the launcher cannot exec the target, so nothing reaches the patcher."""
    missing = str(tmp_path / "not-a-real-script.sh")

    exit_code, issue, _ = run_script(missing, [], SCRIPT_TYPE, timeout=30)

    assert exit_code == ENVIRONMENT_ERROR_EXIT_CODE
    assert missing in issue


@posix_only
def test_the_timeout_message_names_the_absent_input_driver(tmp_path, run_script):
    script = _make_python_script(
        tmp_path,
        "reads_forever",
        """
        import os
        import sys

        while True:
            if not os.read(0, 1):
                sys.stdout.write("stdin closed\\n")
                sys.stdout.flush()
        """,
    )

    exit_code, output, output_file = run_script(script, [], SCRIPT_TYPE, timeout=2)

    assert exit_code == render_utils.TIMEOUT_ERROR_EXIT_CODE
    assert "no input driver was attached" in output.lower()
    assert "no input driver was attached" in Path(output_file).read_text().lower()


def test_the_no_input_diagnostic_names_the_platform_asymmetry_on_windows():
    """POSIX injects the terminal's EOF byte at spawn and ConPTY has no equivalent, so the
    same script behaves differently and the message has to say so."""
    posix = render_utils.no_input_diagnostic("darwin")
    windows = render_utils.no_input_diagnostic("win32")

    assert posix == render_utils.NO_INPUT_DIAGNOSTIC_BASE
    assert windows.startswith(posix)
    assert "end-of-file" in windows
    assert render_utils.NO_INPUT_DIAGNOSTIC == render_utils.no_input_diagnostic(sys.platform)
