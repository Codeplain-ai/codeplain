"""Tests for the PTY launcher and the POSIX terminal backend.

Everything here spawns real processes and allocates real terminals, so the whole module
is POSIX-only. Each helper is responsible for leaving no descriptor and no process
behind — the suite runs against a bounded system PTY limit.
"""

import os
import select
import signal
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import pytest

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="The POSIX PTY backend is not built on Windows.")

pytestmark = posix_only

if sys.platform != "win32":
    from render_machine import pty_exec

REPO_ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = str(REPO_ROOT / "render_machine" / "pty_exec.py")

# Every wait in this module is bounded. These are generous relative to the operations
# they cover, so a failure means a hang rather than a slow machine.
LAUNCH_TIMEOUT = 20.0
SHORT_TIMEOUT = 5.0


def _read_records(fd, timeout):
    """Reads the status pipe to EOF and splits it into (kind, payload) records.

    Deliberately independent of the backend's parser: these cases assert what the
    launcher puts on the wire, not what the parent makes of it.
    """
    deadline = time.monotonic() + timeout
    buffer = b""
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise AssertionError(f"status pipe did not reach EOF within {timeout}s; buffered {buffer!r}")
        readable, _, _ = select.select([fd], [], [], min(remaining, 0.2))
        if not readable:
            continue
        chunk = os.read(fd, 65536)
        if not chunk:
            break
        buffer += chunk

    records = []
    offset = 0
    while offset < len(buffer):
        kind = buffer[offset]
        length = int.from_bytes(buffer[offset + 1 : offset + 5], "big")
        payload = buffer[offset + 5 : offset + 5 + length]
        assert len(payload) == length, f"truncated record in {buffer!r}"
        records.append((kind, payload))
        offset += 5 + length
    return records


class _LauncherSession:
    """Parent side of the launcher protocol, reduced to what these cases need."""

    def __init__(self, proc, master_fd, status_r, ack_w):
        self.proc = proc
        self.master_fd = master_fd
        self.status_r = status_r
        self.ack_w = ack_w
        self.output = bytearray()
        self._drain = threading.Thread(target=self._drain_master, daemon=True)
        self._drain.start()

    def _drain_master(self):
        while True:
            try:
                chunk = os.read(self.master_fd, 65536)
            except OSError:
                return
            if not chunk:
                return
            self.output += chunk

    def ack(self):
        os.write(self.ack_w, b"\x01")

    def close_ack(self):
        if self.ack_w is not None:
            os.close(self.ack_w)
            self.ack_w = None

    def records(self, timeout=LAUNCH_TIMEOUT):
        return _read_records(self.status_r, timeout)

    def wait(self, timeout=LAUNCH_TIMEOUT):
        return self.proc.wait(timeout=timeout)

    def stderr_text(self):
        return self.proc.stderr.read().decode("utf-8", "replace")


@contextmanager
def launcher_session(command, python=None, env=None):
    """Spawns the launcher exactly as the backend does and cleans up unconditionally."""
    master_fd, slave_fd = os.openpty()
    status_r, status_w = os.pipe()
    ack_r, ack_w = os.pipe()
    proc = None
    try:
        proc = subprocess.Popen(
            [
                python or sys.executable,
                "-I",
                "-S",
                LAUNCHER,
                str(slave_fd),
                str(status_w),
                str(ack_r),
                "--",
                *command,
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            pass_fds=(slave_fd, status_w, ack_r),
            close_fds=True,
            env=env,
        )
    finally:
        for fd in (slave_fd, status_w, ack_r):
            os.close(fd)

    session = _LauncherSession(proc, master_fd, status_r, ack_w)
    try:
        yield session
    finally:
        session.close_ack()
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=SHORT_TIMEOUT)
        proc.stderr.close()
        for fd in (master_fd, status_r):
            try:
                os.close(fd)
            except OSError:
                pass


def test_write_record_frames_payloads_that_look_like_markers():
    """A payload that begins with — or equals — a marker byte stays a framed payload."""
    for payload in (b"\x01", b"\x02", b"\x03", b"\x02session ready", b"\x01started", b""):
        read_fd, write_fd = os.pipe()
        try:
            pty_exec._write_record(write_fd, pty_exec.FAILED, payload)
            os.close(write_fd)
            write_fd = None
            framed = os.read(read_fd, 65536)
        finally:
            if write_fd is not None:
                os.close(write_fd)
            os.close(read_fd)

        assert framed == bytes([pty_exec.FAILED]) + len(payload).to_bytes(4, "big") + payload


def test_write_record_bounds_the_payload():
    read_fd, write_fd = os.pipe()
    try:
        pty_exec._write_record(write_fd, pty_exec.FAILED, b"x" * (pty_exec.MAX_PAYLOAD * 2))
        os.close(write_fd)
        write_fd = None
        framed = os.read(read_fd, 65536)
    finally:
        if write_fd is not None:
            os.close(write_fd)
        os.close(read_fd)

    assert int.from_bytes(framed[1:5], "big") == pty_exec.MAX_PAYLOAD
    assert len(framed) == pty_exec.MAX_PAYLOAD + pty_exec.HEADER_SIZE


def test_write_record_completes_across_short_writes(monkeypatch):
    """A short os.write() must not leave a valid header with a truncated payload."""
    read_fd, write_fd = os.pipe()
    real_write = os.write
    chunks = []

    def short_write(fd, data):
        if fd != write_fd:
            return real_write(fd, data)
        count = real_write(fd, data[:1])
        chunks.append(data[:count])
        return count

    payload = b"\x02boom"
    try:
        monkeypatch.setattr(os, "write", short_write)
        pty_exec._write_record(write_fd, pty_exec.FAILED, payload)
        monkeypatch.undo()
        os.close(write_fd)
        write_fd = None
        framed = os.read(read_fd, 65536)
    finally:
        if write_fd is not None:
            os.close(write_fd)
        os.close(read_fd)

    expected = bytes([pty_exec.FAILED]) + len(payload).to_bytes(4, "big") + payload
    assert len(chunks) == len(expected), "the write was not actually fragmented"
    assert b"".join(chunks) == expected
    assert framed == expected


def test_launcher_reports_failure_when_the_ack_pipe_reaches_eof():
    """A parent that dies before acknowledging releases the launcher immediately."""
    with launcher_session(["/bin/sh", "-c", "exit 0"]) as session:
        started = time.monotonic()
        session.close_ack()
        assert session.wait(timeout=SHORT_TIMEOUT) == pty_exec.LAUNCH_FAILURE_EXIT_CODE
        elapsed = time.monotonic() - started
        records = session.records()

    assert elapsed < SHORT_TIMEOUT
    assert [kind for kind, _ in records] == [pty_exec.STARTED, pty_exec.SESSION_READY, pty_exec.FAILED]
    assert b"acknowledgment pipe" in records[-1][1]


def test_launcher_reports_failure_when_the_ack_timeout_expires():
    """A parent that is alive but wedged must not block the launcher forever."""
    env = dict(os.environ, **{pty_exec.ACK_TIMEOUT_ENV: "0.2"})
    with launcher_session(["/bin/sh", "-c", "exit 0"], env=env) as session:
        started = time.monotonic()
        assert session.wait(timeout=SHORT_TIMEOUT) == pty_exec.LAUNCH_FAILURE_EXIT_CODE
        elapsed = time.monotonic() - started
        records = session.records()

    assert elapsed < SHORT_TIMEOUT
    assert [kind for kind, _ in records] == [pty_exec.STARTED, pty_exec.SESSION_READY, pty_exec.FAILED]
    assert b"did not acknowledge" in records[-1][1]


def test_launcher_execs_the_target_after_the_ack():
    with launcher_session(["/bin/sh", "-c", "printf ready; exit 7"]) as session:
        records = []
        deadline = time.monotonic() + LAUNCH_TIMEOUT
        # The ack is written as soon as SESSION_READY has been observed, exactly as the
        # backend does; the status pipe then reaches EOF because exec closes it.
        while time.monotonic() < deadline:
            readable, _, _ = select.select([session.status_r], [], [], 0.2)
            if readable:
                break
        session.ack()
        records = session.records()
        assert session.wait(timeout=SHORT_TIMEOUT) == 7

    assert [kind for kind, _ in records] == [pty_exec.STARTED, pty_exec.SESSION_READY]
    assert b"ready" in bytes(session.output)


def test_target_receives_restored_signal_dispositions():
    """CPython ignores SIGPIPE and that survives exec; _restore_signals() undoes it."""
    with launcher_session(["/bin/sh", "-c", "kill -PIPE $$; exit 0"]) as session:
        deadline = time.monotonic() + LAUNCH_TIMEOUT
        while time.monotonic() < deadline:
            readable, _, _ = select.select([session.status_r], [], [], 0.2)
            if readable:
                break
        session.ack()
        session.records()
        returncode = session.wait(timeout=SHORT_TIMEOUT)

    assert returncode == -signal.SIGPIPE


def _plant_startup_hooks(tmp_path):
    """Builds a throwaway venv whose site-packages runs code at interpreter startup.

    Returns (interpreter, marker_prefix). The venv's own site-packages is used because
    a virtual environment disables the user site directory, so PYTHONUSERBASE cannot
    carry the plant.
    """
    venv_dir = tmp_path / "planted"
    subprocess.run(
        [sys.executable, "-m", "venv", "--without-pip", str(venv_dir)],
        check=True,
        capture_output=True,
        timeout=LAUNCH_TIMEOUT,
    )
    interpreter = venv_dir / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    site_packages = subprocess.run(
        [str(interpreter), "-c", "import sysconfig; print(sysconfig.get_paths()['purelib'])"],
        check=True,
        capture_output=True,
        text=True,
        timeout=LAUNCH_TIMEOUT,
    ).stdout.strip()

    marker_prefix = tmp_path / "startup"
    Path(site_packages, "sitecustomize.py").write_text(
        f"open({str(marker_prefix)!r} + '.sitecustomize', 'w').write('ran')\n"
    )
    Path(site_packages, "zzz_probe.pth").write_text(
        f"import builtins; open({str(marker_prefix)!r} + '.pth', 'w').write('ran')\n"
    )
    return str(interpreter), marker_prefix


def test_launcher_runs_no_startup_customization(tmp_path):
    """`-I -S` is part of the ack barrier's proof: nothing may run before STARTED."""
    interpreter, marker_prefix = _plant_startup_hooks(tmp_path)
    sitecustomize_marker = Path(f"{marker_prefix}.sitecustomize")
    pth_marker = Path(f"{marker_prefix}.pth")

    subprocess.run([interpreter, "-c", "pass"], check=True, capture_output=True, timeout=LAUNCH_TIMEOUT)
    assert sitecustomize_marker.exists(), "the planted sitecustomize.py never ran, so the test proves nothing"
    assert pth_marker.exists(), "the planted .pth never ran, so the test proves nothing"
    sitecustomize_marker.unlink()
    pth_marker.unlink()

    with launcher_session(["/bin/sh", "-c", "exit 0"], python=interpreter) as session:
        deadline = time.monotonic() + LAUNCH_TIMEOUT
        while time.monotonic() < deadline:
            readable, _, _ = select.select([session.status_r], [], [], 0.2)
            if readable:
                break
        session.ack()
        records = session.records()
        assert session.wait(timeout=SHORT_TIMEOUT) == 0

    assert [kind for kind, _ in records] == [pty_exec.STARTED, pty_exec.SESSION_READY]
    assert not sitecustomize_marker.exists()
    assert not pth_marker.exists()


# --------------------------------------------------------------------- backend

if sys.platform != "win32":
    from render_machine import _posix_pty
    from render_machine.terminal_process import (
        InputDisposition,
        TerminalEnvironmentError,
        TerminalLaunchError,
    )

SPAWN_TIMEOUT = 10.0


@contextmanager
def terminal(**spawn_kwargs):
    """Spawns a command through the backend and always tears it down."""
    command = spawn_kwargs.pop("command")
    process = _posix_pty.PosixPtyProcess()
    try:
        process.spawn(command, **spawn_kwargs)
        yield process
    finally:
        try:
            process.terminate_tree(grace=0.05)
        finally:
            process.close()


def wait_for_exit(process, timeout=SPAWN_TIMEOUT):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        returncode = process.poll()
        if returncode is not None:
            return returncode
        time.sleep(0.02)
    raise AssertionError(f"the target did not exit within {timeout}s")


def wait_for_output(process, needle, timeout=SPAWN_TIMEOUT):
    deadline = time.monotonic() + timeout
    collected = ""
    while time.monotonic() < deadline:
        collected += process.read_output()
        if needle in collected:
            return collected
        time.sleep(0.02)
    raise AssertionError(f"{needle!r} never appeared in {collected!r}")


def write_launcher(tmp_path, name, source):
    path = tmp_path / name
    path.write_text(source)
    return str(path)


def stub_launcher(tmp_path, name, body):
    """A launcher that runs the real protocol with `body` applied to the module first."""
    source = (
        "import sys\n"
        f"sys.path.insert(0, {str(REPO_ROOT)!r})\n"
        "from render_machine import pty_exec\n"
        f"{body}\n"
        "pty_exec._run(sys.argv[1:])\n"
    )
    return write_launcher(tmp_path, name, source)


def test_spawn_runs_the_target_and_reports_its_exit_code():
    with terminal(command=["/bin/sh", "-c", "printf hello; exit 3"]) as process:
        wait_for_output(process, "hello")
        assert wait_for_exit(process) == 3


def test_read_output_round_trip():
    with terminal(command=["/bin/sh", "-c", "printf 'one\\ntwo\\n'"]) as process:
        collected = wait_for_output(process, "two")
        assert wait_for_exit(process) == 0

    assert "one" in collected and "two" in collected
    # ONLCR is left at its default, so the terminal supplies the carriage returns.
    assert "\r\n" in collected


def test_poll_returns_none_until_the_target_exits():
    with terminal(command=["/bin/sh", "-c", "sleep 0.3"]) as process:
        assert process.poll() is None
        assert wait_for_exit(process) == 0
        assert process.poll() == 0


def test_handshake_reports_a_launcher_that_reached_our_code_and_failed():
    process = _posix_pty.PosixPtyProcess()
    try:
        with pytest.raises(TerminalLaunchError) as failure:
            process.spawn(["/nonexistent/command/for/tests"])
    finally:
        process.close()

    assert failure.value.exit_code == 69
    assert "the launcher failed" in str(failure.value)


def test_handshake_reports_launcher_invariant_failures(tmp_path, monkeypatch):
    launcher = stub_launcher(
        tmp_path,
        "invariant_launcher.py",
        "def _fail():\n"
        "    raise RuntimeError('PTY is not attached to all three descriptors')\n"
        "pty_exec._assert_invariants = _fail",
    )
    monkeypatch.setattr(_posix_pty, "_LAUNCHER", launcher)

    process = _posix_pty.PosixPtyProcess()
    try:
        with pytest.raises(TerminalLaunchError) as failure:
            process.spawn(["/bin/sh", "-c", "exit 0"])
    finally:
        process.close()

    assert failure.value.exit_code == 69
    assert "PTY is not attached to all three descriptors" in str(failure.value)


def test_handshake_reports_an_interpreter_that_died_before_our_code(tmp_path, monkeypatch):
    launcher = write_launcher(tmp_path, "unparseable_launcher.py", "def broken(:\n")
    monkeypatch.setattr(_posix_pty, "_LAUNCHER", launcher)

    process = _posix_pty.PosixPtyProcess()
    try:
        with pytest.raises(TerminalLaunchError) as failure:
            process.spawn(["/bin/sh", "-c", "exit 0"])
    finally:
        process.close()

    assert failure.value.exit_code == 69
    assert "the interpreter died before running the launcher" in str(failure.value)
    assert "SyntaxError" in str(failure.value)


def test_handshake_reports_a_launcher_that_hangs_before_exec(tmp_path, monkeypatch):
    launcher = write_launcher(tmp_path, "hanging_launcher.py", "import time\ntime.sleep(120)\n")
    monkeypatch.setattr(_posix_pty, "_LAUNCHER", launcher)

    process = _posix_pty.PosixPtyProcess()
    started = time.monotonic()
    try:
        with pytest.raises(TerminalLaunchError) as failure:
            process.spawn(["/bin/sh", "-c", "exit 0"], handshake_timeout=1.0)
    finally:
        process.close()

    assert time.monotonic() - started < SPAWN_TIMEOUT
    assert "hung before exec" in str(failure.value)


def test_handshake_rejects_records_whose_payload_looks_like_a_marker(tmp_path, monkeypatch):
    """A framed error payload equal to a marker byte is still a failure, never a success."""
    for payload in ("b'\\x02'", "b'\\x01'", "b'\\x02 looks like a marker'"):
        launcher = write_launcher(
            tmp_path,
            f"marker_launcher_{abs(hash(payload))}.py",
            "import os, sys\n"
            f"sys.path.insert(0, {str(REPO_ROOT)!r})\n"
            "from render_machine import pty_exec\n"
            "status_fd = int(sys.argv[2])\n"
            "pty_exec._write_record(status_fd, pty_exec.STARTED)\n"
            f"pty_exec._write_record(status_fd, pty_exec.FAILED, {payload})\n"
            "os._exit(127)\n",
        )
        monkeypatch.setattr(_posix_pty, "_LAUNCHER", launcher)

        process = _posix_pty.PosixPtyProcess()
        try:
            with pytest.raises(TerminalLaunchError) as failure:
                process.spawn(["/bin/sh", "-c", "exit 0"])
        finally:
            process.close()

        assert failure.value.exit_code == 69
        assert "the launcher failed" in str(failure.value)


def test_spawn_and_close_leak_no_descriptors():
    def open_fd_count():
        return len(os.listdir("/dev/fd"))

    with terminal(command=["/bin/sh", "-c", "printf warmup"]) as process:
        wait_for_exit(process)

    baseline = open_fd_count()
    for _ in range(3):
        with terminal(command=["/bin/sh", "-c", "printf run"]) as process:
            wait_for_exit(process)
        assert open_fd_count() == baseline


def test_openpty_failure_is_an_environment_error(monkeypatch):
    monkeypatch.setattr(_posix_pty.os, "openpty", lambda: (_ for _ in ()).throw(OSError(23, "too many open files")))
    process = _posix_pty.PosixPtyProcess()
    try:
        with pytest.raises(TerminalEnvironmentError) as failure:
            process.spawn(["/bin/sh", "-c", "exit 0"])
    finally:
        process.close()

    assert failure.value.exit_code == 69
    assert "too many open files" in str(failure.value)


def test_write_input_reports_whole_item_admission():
    with terminal(command=["/bin/sh", "-c", "read line; printf 'got:%s' \"$line\""], input_driver=object()) as process:
        result = process.write_input(b"payload\n")
        assert result.disposition is InputDisposition.ACCEPTED
        assert result.accepted_bytes == len(b"payload\n")
        wait_for_output(process, "got:payload")
        assert wait_for_exit(process) == 0


def test_write_input_reports_backpressure_for_an_oversized_item():
    with terminal(command=["/bin/sh", "-c", "sleep 5"], input_driver=object()) as process:
        result = process.write_input(b"x" * (_posix_pty.MAX_INPUT_ITEM_BYTES + 1))
        assert result.disposition is InputDisposition.BACKPRESSURE
        assert result.accepted_bytes == 0
