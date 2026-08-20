"""Manual diagnostic: run `node --version` under the ADR-001 stale controlling-TTY topology.

Usage:

    python tests/diagnostics/node_tty_probe.py

Runs Node with fd 0 bound to a terminal owned by a session the process has just left —
the same topology `tests/test_pty_characterization.py` constructs — and prints platform,
Node version, exit status or terminating signal, and captured stderr.

This is not collected by pytest and never gates anything: the observed outcome varies by
macOS and Node version. A green result does not invalidate ADR-001; it only narrows the
blast radius to specific macOS/Node combinations. The script always exits 0, including
when Node is not installed.
"""

import json
import os
import platform
import re
import shutil
import signal
import subprocess
import sys
import time

NODE_TIMEOUT_SECONDS = 30
HARNESS_TIMEOUT_SECONDS = 60
CLEANUP_TIMEOUT_SECONDS = 10
KILL_POLL_TIMEOUT_SECONDS = 5

# Node leaves the harness's session, so the outer timeout path has to kill it by pid;
# the harness announces the pid on stderr before waiting on it.
NODE_PID_PATTERN = re.compile(r"^node_pid=(\d+)$", re.MULTILINE)

# Source of the middle process: it owns the terminal, then spawns Node into a new
# session with the terminal still on fd 0.
HARNESS_SOURCE = """
import fcntl
import json
import os
import subprocess
import sys
import termios

NODE_TIMEOUT_SECONDS = %d
CLEANUP_TIMEOUT_SECONDS = %d


def reap(process):
    process.kill()
    try:
        process.communicate(timeout=CLEANUP_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        pass


def main():
    node_path = sys.argv[1]

    if os.getsid(0) != os.getpid():
        os.setsid()

    report = {"harness_sid": os.getsid(0), "error": None}
    master_fd, slave_fd = os.openpty()
    process = None
    try:
        fcntl.ioctl(slave_fd, termios.TIOCSCTTY, 0)
        report["controlling_tty"] = os.ttyname(slave_fd)

        process = subprocess.Popen(
            [node_path, "--version"],
            stdin=slave_fd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        sys.stderr.write("node_pid=%%d\\n" %% process.pid)
        sys.stderr.flush()

        stdout, stderr = process.communicate(timeout=NODE_TIMEOUT_SECONDS)
        report["returncode"] = process.returncode
        report["stdout"] = stdout.decode(errors="replace")
        report["stderr"] = stderr.decode(errors="replace")
    except subprocess.TimeoutExpired:
        reap(process)
        report["error"] = "node did not exit within %%d seconds" %% NODE_TIMEOUT_SECONDS
    except Exception as exc:
        if process is not None and process.poll() is None:
            reap(process)
        report["error"] = "%%s: %%s" %% (type(exc).__name__, exc)
    finally:
        # The master is held open for the lifetime of the child so reads on the slave
        # cannot fail with EIO.
        os.close(slave_fd)
        os.close(master_fd)

    sys.stdout.write(json.dumps(report))
    sys.stdout.flush()


main()
""" % (
    NODE_TIMEOUT_SECONDS,
    CLEANUP_TIMEOUT_SECONDS,
)


def kill_process_group(pid):
    """Best-effort teardown of the orphaned child; it is a session leader, so pgid == pid."""
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


def reap(process):
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


def describe_platform():
    if sys.platform == "darwin":
        completed = subprocess.run(["sw_vers"], capture_output=True, timeout=30)
        return completed.stdout.decode(errors="replace").strip()
    return " ".join(platform.uname())


def node_version(node_path):
    completed = subprocess.run([node_path, "--version"], capture_output=True, timeout=NODE_TIMEOUT_SECONDS)
    return completed.stdout.decode(errors="replace").strip() or "unknown"


def describe_exit(report):
    if report.get("error"):
        return report["error"]

    returncode = report.get("returncode")
    if returncode is None:
        return "unknown"
    if returncode < 0:
        return "terminated by signal %d (%s)" % (-returncode, signal.Signals(-returncode).name)
    return "exit status %d" % returncode


def run_probe(node_path):
    process = subprocess.Popen(
        [sys.executable, "-c", HARNESS_SOURCE, node_path],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        stdout, stderr = process.communicate(timeout=HARNESS_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        stdout, stderr = reap(process)
        match = NODE_PID_PATTERN.search(stderr.decode(errors="replace"))
        if match:
            kill_process_group(int(match.group(1)))
        return {"error": "harness did not finish within %d seconds" % HARNESS_TIMEOUT_SECONDS}

    if process.returncode != 0 or not stdout.strip():
        return {"error": "harness failed: %s" % stderr.decode(errors="replace").strip()}
    return json.loads(stdout.decode())


def main():
    print("=== ADR-001 stale controlling-TTY probe (diagnostic, non-gating) ===")
    print("platform:")
    print(describe_platform())

    if sys.platform == "win32":
        print("node: not probed - the topology relies on setsid() and TIOCSCTTY, which are POSIX-only")
        return 0

    node_path = shutil.which("node")
    if node_path is None:
        print("node: not installed - nothing to probe")
        return 0

    print("node path: %s" % node_path)
    print("node version: %s" % node_version(node_path))

    report = run_probe(node_path)
    print("controlling tty: %s" % report.get("controlling_tty", "n/a"))
    print("result: %s" % describe_exit(report))
    print("stdout: %s" % (report.get("stdout", "").strip() or "<empty>"))
    print("stderr: %s" % (report.get("stderr", "").strip() or "<empty>"))
    print("A green result does not invalidate ADR-001; it narrows the blast radius.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # the diagnostic must never fail the caller
        print("probe could not run: %s: %s" % (type(exc).__name__, exc))
        sys.exit(0)
