"""Characterization of the stale controlling-TTY topology described in ADR-001.

The harness rebuilds the process topology that `execute_script()` produces today —
a child spawned with `start_new_session=True` whose fd 0 still points at a terminal
owned by the session the child has just left — without touching any production code.
It documents the defect; it never validates a fix.

Three levels are needed. `os.openpty()` alone yields a terminal owned by no session,
which is a weaker state than the one under study, so a middle process takes the slave
as its controlling terminal via TIOCSCTTY. That process is a subprocess rather than
the test runner itself because `setsid()` in pytest would detach the runner.

    pytest process
    └── harness subprocess      setsid(), then TIOCSCTTY on the slave
        └── probe grandchild    start_new_session=True, fd 0 = slave

The same two-level spawn shape with fd 0 on `/dev/null` is exercised as a control
case, documenting why the defect is reachable only from an interactive terminal.

Only the constructed topology is asserted. The `termios.tcgetattr(0)` outcome is
recorded rather than asserted, because it varies by host.
"""

import json
import os
import re
import signal
import subprocess
import sys
import time

import pytest

HARNESS_TIMEOUT_SECONDS = 30
CLEANUP_TIMEOUT_SECONDS = 10
KILL_POLL_TIMEOUT_SECONDS = 5

# The grandchild leaves the harness's session, so the outer timeout path has to kill it
# by pid; the harness announces the pid on stderr before waiting on it.
GRANDCHILD_PID_PATTERN = re.compile(r"^grandchild_pid=(\d+)$", re.MULTILINE)

# Source of the grandchild. Reports the state of fd 0 as JSON on stdout.
PROBE_SOURCE = """
import errno
import json
import os
import sys
import termios

report = {
    "pid": os.getpid(),
    "sid": os.getsid(0),
    "isatty_stdin": os.isatty(0),
}
report["is_session_leader"] = report["sid"] == report["pid"]

try:
    termios.tcgetattr(0)
    report["tcgetattr"] = "ok"
    report["tcgetattr_errno"] = None
    report["tcgetattr_errno_name"] = None
except (OSError, termios.error) as exc:
    # termios.error is not an OSError and carries its errno only in args[0].
    code = getattr(exc, "errno", None)
    if code is None and exc.args:
        code = exc.args[0]
    report["tcgetattr"] = "error"
    report["tcgetattr_errno"] = code
    report["tcgetattr_errno_name"] = errno.errorcode.get(code, str(code))

sys.stdout.write(json.dumps(report))
sys.stdout.flush()
"""

# Source of the middle process. Owns the terminal, then spawns the grandchild.
HARNESS_SOURCE = """
import fcntl
import json
import os
import subprocess
import sys
import termios

PROBE_TIMEOUT_SECONDS = 20
CLEANUP_TIMEOUT_SECONDS = 10


def reap(process):
    process.kill()
    try:
        process.communicate(timeout=CLEANUP_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        pass


def main():
    mode = sys.argv[1]
    probe_source = sys.argv[2]

    if os.getsid(0) != os.getpid():
        os.setsid()

    report = {"harness_pid": os.getpid(), "harness_sid": os.getsid(0), "mode": mode, "error": None}
    open_fds = []
    process = None
    try:
        if mode == "pty":
            master_fd, slave_fd = os.openpty()
            open_fds.extend([slave_fd, master_fd])
            # The slave becomes this session's controlling terminal; the grandchild
            # then leaves the session while keeping the slave on fd 0.
            fcntl.ioctl(slave_fd, termios.TIOCSCTTY, 0)
            report["controlling_tty"] = os.ttyname(slave_fd)
            stdin_fd = slave_fd
        else:
            stdin_fd = os.open(os.devnull, os.O_RDONLY)
            open_fds.append(stdin_fd)
            report["controlling_tty"] = None

        process = subprocess.Popen(
            [sys.executable, "-c", probe_source],
            stdin=stdin_fd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        sys.stderr.write("grandchild_pid=%d\\n" % process.pid)
        sys.stderr.flush()

        stdout, stderr = process.communicate(timeout=PROBE_TIMEOUT_SECONDS)
        report["probe_exit_code"] = process.returncode
        report["probe_stderr"] = stderr.decode(errors="replace")
        report["probe"] = json.loads(stdout.decode()) if stdout.strip() else None
    except subprocess.TimeoutExpired:
        reap(process)
        report["error"] = "probe did not report within %d seconds" % PROBE_TIMEOUT_SECONDS
    except Exception as exc:
        if process is not None and process.poll() is None:
            reap(process)
        report["error"] = "%s: %s" % (type(exc).__name__, exc)
    finally:
        # The PTY master is held open for the lifetime of the grandchild so reads on
        # the slave cannot fail with EIO.
        for fd in open_fds:
            os.close(fd)

    sys.stdout.write(json.dumps(report))
    sys.stdout.flush()


main()
"""

pytestmark = pytest.mark.skipif(
    sys.platform == "win32",
    reason="The topology relies on setsid() and TIOCSCTTY, which are POSIX-only.",
)


def _kill_process_group(pid):
    """Best-effort teardown of the orphaned grandchild; it is a session leader, so pgid == pid."""
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(pid, sig)
        except (ProcessLookupError, PermissionError):
            return
        deadline = time.monotonic() + KILL_POLL_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            try:
                os.killpg(pid, 0)
            except (ProcessLookupError, PermissionError):
                return
            time.sleep(0.05)


def _reap(process):
    """Terminates the harness, escalating to SIGKILL, and returns whatever it had written."""
    if process.poll() is None:
        process.terminate()
        try:
            return process.communicate(timeout=CLEANUP_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            process.kill()
    try:
        return process.communicate(timeout=CLEANUP_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        return b"", b""


def _run_harness(mode):
    process = subprocess.Popen(
        [sys.executable, "-c", HARNESS_SOURCE, mode, PROBE_SOURCE],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        stdout, stderr = process.communicate(timeout=HARNESS_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        stdout, stderr = _reap(process)
        match = GRANDCHILD_PID_PATTERN.search(stderr.decode(errors="replace"))
        if match:
            _kill_process_group(int(match.group(1)))
        pytest.fail("harness did not finish within %d seconds" % HARNESS_TIMEOUT_SECONDS)

    stderr_text = stderr.decode(errors="replace")
    assert process.returncode == 0, "harness failed: %s" % stderr_text

    stdout_text = stdout.decode(errors="replace")
    assert stdout_text.strip(), "harness produced no report; stderr: %s" % stderr_text

    report = json.loads(stdout_text)
    assert report["error"] is None, "harness could not build the topology: %s" % report["error"]
    assert report["probe"] is not None, "grandchild produced no report: %s" % report.get("probe_stderr")
    return report


@pytest.fixture(scope="module")
def stale_controlling_tty_report():
    """Runs the three-level harness once and returns the grandchild's report."""
    return _run_harness("pty")


@pytest.fixture(scope="module")
def non_tty_stdin_report():
    """Runs the same spawn shape with fd 0 on /dev/null and returns the grandchild's report."""
    return _run_harness("devnull")


def test_grandchild_stdin_is_a_terminal(stale_controlling_tty_report):
    probe = stale_controlling_tty_report["probe"]

    assert probe["isatty_stdin"] is True
    assert stale_controlling_tty_report["probe_exit_code"] == 0


def test_grandchild_left_the_session_that_owns_the_terminal(stale_controlling_tty_report):
    probe = stale_controlling_tty_report["probe"]

    assert probe["is_session_leader"] is True
    assert probe["sid"] != stale_controlling_tty_report["harness_sid"]


def test_grandchild_tcgetattr_outcome_is_recorded(stale_controlling_tty_report, record_property):
    probe = stale_controlling_tty_report["probe"]

    record_property("platform", sys.platform)
    record_property("tcgetattr", probe["tcgetattr"])
    record_property("tcgetattr_errno", probe["tcgetattr_errno_name"])
    print(
        "stale controlling TTY on %s: tcgetattr=%s errno=%s"
        % (sys.platform, probe["tcgetattr"], probe["tcgetattr_errno_name"])
    )

    # The outcome varies by host, so only its presence is asserted.
    assert probe["tcgetattr"] in ("ok", "error")


def test_grandchild_stdin_is_not_a_terminal_without_a_pty(non_tty_stdin_report):
    """Control case: non-interactive callers give the child a non-TTY fd 0."""
    probe = non_tty_stdin_report["probe"]

    assert probe["isatty_stdin"] is False
    assert probe["is_session_leader"] is True
    assert probe["sid"] != non_tty_stdin_report["harness_sid"]
    assert non_tty_stdin_report["probe_exit_code"] == 0
