"""Tests for the PTY launcher and the POSIX terminal backend.

Everything here spawns real processes and allocates real terminals, so the whole module
is POSIX-only. Each helper is responsible for leaving no descriptor and no process
behind — the suite runs against a bounded system PTY limit.
"""

import errno
import os
import select
import signal
import subprocess
import sys
import termios
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
    from plain2code_exceptions import RenderCancelledError
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


def fail_nth_call(monkeypatch, module, name, error, nth):
    """Lets the first `nth - 1` calls through and fails the one after them."""
    real = getattr(module, name)
    state = {"calls": 0}

    def failing(*args, **kwargs):
        state["calls"] += 1
        if state["calls"] == nth:
            raise error
        return real(*args, **kwargs)

    monkeypatch.setattr(module, name, failing)
    return state


@pytest.mark.parametrize("nth", [1, 2, 3, 4])
def test_a_failing_channel_pipe_rolls_back_the_descriptors_already_opened(monkeypatch, nth):
    """One case per os.pipe() in _open_channels: the earlier pairs must not survive it."""
    baseline = open_fd_count()
    state = fail_nth_call(monkeypatch, _posix_pty.os, "pipe", OSError(errno.EMFILE, "too many open files"), nth)

    process = _posix_pty.PosixPtyProcess()
    with pytest.raises(TerminalEnvironmentError) as failure:
        process.spawn(["/bin/sh", "-c", "exit 0"])
    process.close()
    monkeypatch.undo()

    assert state["calls"] == nth
    assert failure.value.exit_code == 69
    assert "too many open files" in str(failure.value)
    assert open_fd_count() == baseline


@pytest.mark.parametrize("nth", [2, 3])
def test_a_failing_doorbell_mode_change_rolls_back_every_channel(monkeypatch, nth):
    """The doorbell's os.set_blocking() calls run with all four pipe pairs already open."""
    baseline = open_fd_count()
    error = OSError(errno.EBADF, "injected set_blocking failure")
    state = fail_nth_call(monkeypatch, _posix_pty.os, "set_blocking", error, nth)

    process = _posix_pty.PosixPtyProcess()
    with pytest.raises(TerminalEnvironmentError) as failure:
        process.spawn(["/bin/sh", "-c", "exit 0"])
    process.close()
    monkeypatch.undo()

    assert state["calls"] == nth  # the first call belongs to the master, not to the doorbell
    assert failure.value.exit_code == 69
    assert "injected set_blocking failure" in str(failure.value)
    assert open_fd_count() == baseline


def test_a_failing_reader_thread_construction_rolls_back_every_channel(monkeypatch):
    """The last construction step in _open_channels; nothing has an owner before it."""
    baseline = open_fd_count()
    real_thread = _posix_pty.threading.Thread

    def failing_thread(*args, **kwargs):
        if kwargs.get("name") == "codeplain-pty-reader":
            raise RuntimeError("can't start new thread")
        return real_thread(*args, **kwargs)

    monkeypatch.setattr(_posix_pty.threading, "Thread", failing_thread)
    process = _posix_pty.PosixPtyProcess()
    with pytest.raises(TerminalEnvironmentError) as failure:
        process.spawn(["/bin/sh", "-c", "exit 0"])
    process.close()
    monkeypatch.undo()

    assert failure.value.exit_code == 69
    assert "can't start new thread" in str(failure.value)
    assert process._bundle is None and process._reader is None  # nothing was published
    assert open_fd_count() == baseline


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


class _OversizedItem:
    """Reports a size but refuses to be copied, so a copy-before-validate is visible."""

    def __len__(self):
        return _posix_pty.MAX_INPUT_ITEM_BYTES + 1

    def __bytes__(self):
        raise AssertionError("the oversized item was copied before it was rejected")


def test_an_oversized_item_is_rejected_before_it_is_copied():
    queue = _posix_pty._InputQueue()
    result, receipt = queue.submit(_OversizedItem())

    assert result.disposition is InputDisposition.BACKPRESSURE
    assert result.accepted_bytes == 0
    assert receipt.resolutions == 1
    assert queue.pending_items() == 0


def test_an_empty_item_never_becomes_a_queue_entry():
    """Zero-length items cost no bytes, so admitting them would grow the queue unbounded."""
    queue = _posix_pty._InputQueue()
    for _ in range(10_000):
        result, receipt = queue.submit(b"")
        assert result.disposition is InputDisposition.ACCEPTED
        assert result.accepted_bytes == 0
        assert receipt.resolutions == 1

    assert queue.pending_items() == 0
    assert queue.pending_bytes() == 0
    assert not queue.has_pending()


def test_the_input_queue_bounds_the_item_count_as_well_as_the_bytes():
    """Single-byte items exhaust the item budget long before the byte budget."""
    queue = _posix_pty._InputQueue()
    accepted = 0
    while queue.submit(b"x")[0].disposition is InputDisposition.ACCEPTED:
        accepted += 1
        if accepted > _posix_pty.MAX_PENDING_INPUT_ITEMS:
            raise AssertionError("the queue admitted more items than its item budget allows")

    assert accepted == _posix_pty.MAX_PENDING_INPUT_ITEMS - _posix_pty.RESERVED_INPUT_ITEMS
    assert queue.pending_bytes() == accepted
    assert queue.pending_bytes() < _posix_pty.MAX_PENDING_INPUT_BYTES, "the byte budget was not the binding limit"
    # The reserved partition is an admission partition, so control items still fit.
    assert queue.submit(b"x", reserved=True)[0].disposition is InputDisposition.ACCEPTED


# ------------------------------------------------------------------- lifecycle


def make_script(directory, name, body):
    """Writes an executable /bin/sh script and returns its absolute path."""
    path = Path(directory) / f"{name}.sh"
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(0o755)
    return str(path)


def wait_until_gone(pid, timeout=SPAWN_TIMEOUT):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.02)
    return False


def reported_pid(process, label, timeout=SPAWN_TIMEOUT):
    collected = wait_for_output(process, f"{label}:", timeout)
    for line in collected.replace("\r", "").splitlines():
        if line.startswith(f"{label}:"):
            return int(line.split(":", 1)[1])
    raise AssertionError(f"no {label} pid in {collected!r}")


def test_close_returns_while_the_reader_is_parked_and_a_descendant_holds_the_slave(tmp_path):
    """The regression guard for the verified macOS close()-on-a-blocked-read hang.

    The failure mode is a hang rather than an exception, so the assertion is on elapsed
    time: `close()` must return and the reader must join while both the leader and a
    descendant still hold the slave open.
    """
    script = make_script(tmp_path, "holder", "sleep 20 &\nprintf 'descendant:%s\\n' \"$!\"\nsleep 20\n")
    process = _posix_pty.PosixPtyProcess()
    try:
        process.spawn([script])
        descendant = reported_pid(process, "descendant")
        assert process.poll() is None
        assert process._reader is not None and process._reader.is_alive()

        started = time.monotonic()
        process.close()
        elapsed = time.monotonic() - started
    finally:
        process.terminate_tree(grace=0.05)
        process.close()

    assert elapsed < _posix_pty.DRAIN_DEADLINE_SECONDS + SHORT_TIMEOUT
    assert not process._reader.is_alive()
    assert process.reader_exc is None
    assert wait_until_gone(descendant)


def test_reader_exits_cleanly_when_the_leader_exits_with_a_descendant_on_the_slave(tmp_path):
    """Either the hangup or the last slave close ends the stream; neither may raise."""
    script = make_script(tmp_path, "leaver", "sleep 20 &\nprintf 'descendant:%s\\n' \"$!\"\nexit 0\n")
    process = _posix_pty.PosixPtyProcess()
    try:
        process.spawn([script])
        descendant = reported_pid(process, "descendant")
        assert wait_for_exit(process) == 0
        started = time.monotonic()
        process.close()
        elapsed = time.monotonic() - started
    finally:
        process.terminate_tree(grace=0.05)
        process.close()
        if not wait_until_gone(descendant, timeout=0.5):
            os.kill(descendant, signal.SIGKILL)

    assert elapsed < _posix_pty.DRAIN_DEADLINE_SECONDS + SHORT_TIMEOUT
    assert process.reader_exc is None


def test_cancellation_inside_the_ack_window_leaves_nothing_behind():
    """Deterministic through the delayed-ack hook: the window is opened, not raced.

    The cancellation waits for the hook to be entered, so it can never land before
    SESSION_READY however slowly the launcher gets there.
    """
    stop_event = threading.Event()
    process = _posix_pty.PosixPtyProcess()
    entered = threading.Event()
    real_wait_pre_ack = process._wait_pre_ack

    def recording_wait_pre_ack(delay, deadline):
        entered.set()
        real_wait_pre_ack(delay, deadline)

    def cancel_inside_the_window():
        if entered.wait(SPAWN_TIMEOUT):
            stop_event.set()

    process._wait_pre_ack = recording_wait_pre_ack
    canceller = threading.Thread(target=cancel_inside_the_window, daemon=True)
    canceller.start()
    try:
        with pytest.raises(RenderCancelledError):
            process.spawn(["/bin/sh", "-c", "sleep 30"], stop_event=stop_event, pre_ack_delay=5.0)
    finally:
        canceller.join(timeout=SHORT_TIMEOUT)
        process.close()

    assert entered.is_set()
    launcher_pid = process._proc.pid
    assert process._proc.returncode is not None
    assert wait_until_gone(launcher_pid)


def test_cancellation_after_the_ack_reaps_a_forked_descendant(tmp_path):
    script = make_script(
        tmp_path,
        "forker",
        "sleep 30 &\nprintf 'descendant:%s\\n' \"$!\"\nsleep 30\n",
    )
    stop_event = threading.Event()
    process = _posix_pty.PosixPtyProcess()
    try:
        process.spawn([script], stop_event=stop_event)
        descendant = reported_pid(process, "descendant")
        stop_event.set()
        process.terminate_tree(grace=0.2)
    finally:
        process.close()

    assert wait_until_gone(descendant)


def test_launcher_ack_timeout_beats_the_parents_ack():
    """The parent's write hits a closed pipe; the launcher's own reason must surface."""
    env = dict(os.environ, **{pty_exec.ACK_TIMEOUT_ENV: "0.2"})
    process = _posix_pty.PosixPtyProcess()
    try:
        with pytest.raises(TerminalLaunchError) as failure:
            process.spawn(["/bin/sh", "-c", "exit 0"], env=env, pre_ack_delay=2.0, handshake_timeout=10.0)
    finally:
        process.close()

    assert failure.value.exit_code == 69
    assert "did not acknowledge" in str(failure.value)


def test_codeplains_own_process_group_is_never_signalled(tmp_path, monkeypatch):
    signalled = []
    real_killpg = os.killpg

    def recording_killpg(pgid, sig):
        signalled.append(pgid)
        return real_killpg(pgid, sig)

    monkeypatch.setattr(_posix_pty.os, "killpg", recording_killpg)
    own_pgid = os.getpgrp()

    # Cancellation before the handshake completes, where no group has been recorded yet.
    hanging = write_launcher(tmp_path, "hang.py", "import time\ntime.sleep(120)\n")
    monkeypatch.setattr(_posix_pty, "_LAUNCHER", hanging)
    stop_event = threading.Event()
    threading.Timer(0.2, stop_event.set).start()
    process = _posix_pty.PosixPtyProcess()
    try:
        with pytest.raises(RenderCancelledError):
            process.spawn(["/bin/sh", "-c", "sleep 30"], stop_event=stop_event, handshake_timeout=10.0)
    finally:
        process.close()

    # Cancellation inside the ack window, and termination after a normal spawn.
    monkeypatch.undo()
    monkeypatch.setattr(_posix_pty.os, "killpg", recording_killpg)
    stop_event = threading.Event()
    threading.Timer(0.2, stop_event.set).start()
    process = _posix_pty.PosixPtyProcess()
    try:
        with pytest.raises(RenderCancelledError):
            process.spawn(["/bin/sh", "-c", "sleep 30"], stop_event=stop_event, pre_ack_delay=5.0)
    finally:
        process.close()

    with terminal(command=["/bin/sh", "-c", "sleep 30"]) as running:
        running.terminate_tree(grace=0.1)

    assert own_pgid not in signalled
    assert signalled, "the recorded group should still be signalled on the ordinary path"


def test_killpg_has_exactly_one_call_site():
    """A bare os.killpg(os.getpgid(...)) anywhere is the F1 defect returning."""
    source = Path(_posix_pty.__file__).read_text()
    assert source.count("os.killpg(") == 1
    assert "getpgid" not in source


def test_spawn_without_a_stop_event_runs_end_to_end():
    process = _posix_pty.PosixPtyProcess()
    try:
        process.spawn(["/bin/sh", "-c", "printf done; exit 0"])
        wait_for_output(process, "done")
        assert wait_for_exit(process) == 0
    finally:
        process.close()


def test_spawn_time_veof_lets_a_single_read_script_exit_promptly(tmp_path):
    script = make_script(tmp_path, "single_read", "read line\nprintf 'read-returned:%s\\n' \"$?\"\nexit 0\n")
    started = time.monotonic()
    with terminal(command=[script]) as process:
        wait_for_output(process, "read-returned:")
        assert wait_for_exit(process) == 0
    assert time.monotonic() - started < SHORT_TIMEOUT


def test_spawn_time_veof_leaves_no_trace(tmp_path):
    """Echo is disabled around the injection, so a silent command stays byte-empty."""
    script = make_script(tmp_path, "silent", "exit 0\n")
    with terminal(command=[script]) as process:
        assert wait_for_exit(process) == 0
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            time.sleep(0.05)
        raw = process.read_raw_output()
        decoded = process.read_output()

    assert raw == b""
    assert decoded == ""


def test_a_slow_silent_script_runs_to_completion_untouched(tmp_path):
    """The regression guard against the rejected silence timer."""
    script = make_script(tmp_path, "slow_silent", "sleep 1.5\nprintf finished\nexit 0\n")
    with terminal(command=[script]) as process:
        wait_for_output(process, "finished")
        assert wait_for_exit(process) == 0


def arm_fault(process, method, error=None):
    """Wraps one reader entry point so a failure can be injected at a chosen moment."""
    real = getattr(process, method)
    state = {"armed": False, "calls": 0}

    def faulty(*args, **kwargs):
        state["calls"] += 1
        if state["armed"]:
            raise error if error is not None else OSError(errno.EBADF, "injected reader failure")
        return real(*args, **kwargs)

    setattr(process, method, faulty)
    return state


def open_fd_count():
    return len(os.listdir("/dev/fd"))


def test_close_is_idempotent_and_survives_a_partial_spawn(monkeypatch):
    baseline = open_fd_count()

    monkeypatch.setattr(
        _posix_pty.subprocess, "Popen", lambda *a, **k: (_ for _ in ()).throw(OSError(2, "no interpreter"))
    )
    process = _posix_pty.PosixPtyProcess()
    with pytest.raises(TerminalEnvironmentError):
        process.spawn(["/bin/sh", "-c", "exit 0"])
    process.close()
    process.close()

    assert open_fd_count() == baseline


def test_failure_before_the_reader_starts_closes_the_parent_owned_descriptors(monkeypatch):
    """No reader exists to close them, so spawn()'s except path has to."""
    baseline = open_fd_count()
    monkeypatch.setattr(
        _posix_pty.subprocess, "Popen", lambda *a, **k: (_ for _ in ()).throw(OSError(2, "no interpreter"))
    )
    process = _posix_pty.PosixPtyProcess()
    with pytest.raises(TerminalEnvironmentError):
        process.spawn(["/bin/sh", "-c", "exit 0"])

    assert process._bundle is not None
    assert process._bundle.owner == "parent"
    assert process._bundle.master_fd is None
    assert process._bundle.wakeup_r is None
    assert process._bundle.err_w is None
    assert open_fd_count() == baseline


def test_closing_err_r_transfers_ownership_rather_than_sharing_it(tmp_path):
    """A descriptor number is reusable the instant it is freed, so the field is swapped
    to None before the close and only what the swap returned is closed."""
    process = _posix_pty.PosixPtyProcess()
    unrelated = None
    unrelated_path = tmp_path / "unrelated.txt"
    try:
        process.spawn(["/bin/sh", "-c", "sleep 5"])
        process._close_owned("_err_r")  # the transfer the handshake performs on a reader edge
        unrelated = os.open(str(unrelated_path), os.O_CREAT | os.O_RDWR, 0o600)
        process.terminate_tree(grace=0.05)
        process.close()
        process.close()
        os.write(unrelated, b"still mine")  # close() must not have taken this number
    finally:
        if unrelated is not None:
            os.close(unrelated)
        process.close()

    assert unrelated_path.read_bytes() == b"still mine"


def test_descriptor_counts_are_stable_across_failing_spawns(tmp_path, monkeypatch):
    """Covers the ack pair and the launcher's stderr as well as the reader bundle."""
    hanging = write_launcher(tmp_path, "hang_fd.py", "import time\ntime.sleep(120)\n")
    process = _posix_pty.PosixPtyProcess()
    try:
        process.spawn(["/bin/sh", "-c", "exit 0"])
        wait_for_exit(process)
    finally:
        process.close()

    baseline = open_fd_count()
    for _ in range(2):
        failing = _posix_pty.PosixPtyProcess()
        with pytest.raises(TerminalLaunchError):
            failing.spawn(["/nonexistent/command/for/tests"])
        failing.close()
        assert open_fd_count() == baseline

        monkeypatch.setattr(_posix_pty, "_LAUNCHER", hanging)
        hung = _posix_pty.PosixPtyProcess()
        with pytest.raises(TerminalLaunchError):
            hung.spawn(["/bin/sh", "-c", "exit 0"], handshake_timeout=0.5)
        hung.close()
        monkeypatch.undo()
        assert open_fd_count() == baseline


def test_a_launcher_that_floods_stderr_does_not_stall_the_handshake(tmp_path, monkeypatch):
    flood = write_launcher(
        tmp_path,
        "flood.py",
        "import os\n"
        "payload = b'HEAD' + b'x' * (512 * 1024) + b'TAIL'\n"
        "while payload:\n"
        "    payload = payload[os.write(2, payload):]\n"
        "os._exit(3)\n",
    )
    monkeypatch.setattr(_posix_pty, "_LAUNCHER", flood)

    process = _posix_pty.PosixPtyProcess()
    started = time.monotonic()
    try:
        with pytest.raises(TerminalLaunchError) as failure:
            process.spawn(["/bin/sh", "-c", "exit 0"], handshake_timeout=SPAWN_TIMEOUT)
    finally:
        process.close()

    assert time.monotonic() - started < SPAWN_TIMEOUT
    assert "the interpreter died before running the launcher" in str(failure.value)
    diagnostic = process.launcher_stderr
    assert diagnostic.total > 512 * 1024, "the flood was not read to completion"
    text = diagnostic.text()
    assert text.startswith("HEAD") and text.endswith("TAIL")
    assert len(text) < 2 * _posix_pty.LAUNCHER_STDERR_CAP_BYTES + 128


def test_escalation_is_driven_by_the_clock_not_by_the_leaders_exit(tmp_path):
    """The leader dies on SIGTERM at once; the descendant that ignores it must still go."""
    script = make_script(
        tmp_path,
        "escalation",
        "( trap '' TERM; printf 'descendant:%s\\n' \"$$\"; sleep 30 ) &\nsleep 30\n",
    )
    process = _posix_pty.PosixPtyProcess()
    try:
        process.spawn([script])
        descendant = reported_pid(process, "descendant")
        process.terminate_tree(grace=0.3)
    finally:
        process.close()

    assert wait_until_gone(descendant)


def test_teardown_tolerates_a_zombie_only_group(tmp_path):
    """The graceful path: the leader has exited and only our unreaped zombie remains."""
    script = make_script(tmp_path, "quick", "printf bye\nexit 0\n")
    process = _posix_pty.PosixPtyProcess()
    try:
        process.spawn([script])
        wait_for_output(process, "bye")
        time.sleep(0.3)  # let the leader exit without reaping it through poll()
        process.terminate_tree(grace=0.1)
    finally:
        process.close()

    assert process._proc.returncode is not None  # reaped despite the EPERM answer


def test_teardown_tolerates_a_permission_error_from_killpg(tmp_path, monkeypatch):
    """macOS answers EPERM, not ESRCH, for a group holding only our zombie leader."""
    script = make_script(tmp_path, "quick_eperm", "sleep 30\n")
    process = _posix_pty.PosixPtyProcess()
    try:
        process.spawn([script])

        def denying_killpg(pgid, sig):
            raise PermissionError(1, "Operation not permitted")

        monkeypatch.setattr(_posix_pty.os, "killpg", denying_killpg)
        process.terminate_tree(grace=0.05)
        monkeypatch.undo()
    finally:
        process.terminate_tree(grace=0.05)
        process.close()

    assert process._reaped


def test_the_grace_period_survives_cancellation(tmp_path):
    """stop_event is already set when teardown begins, so the grace runs off its own clock."""
    script = make_script(
        tmp_path,
        "graceful",
        "trap 'printf handled; exit 0' TERM\nprintf ready\nwhile true; do sleep 0.05; done\n",
    )
    stop_event = threading.Event()
    process = _posix_pty.PosixPtyProcess()
    try:
        process.spawn([script], stop_event=stop_event)
        wait_for_output(process, "ready")
        stop_event.set()
        process.terminate_tree(grace=2.0)
        collected = process.read_output()
    finally:
        process.close()

    assert "handled" in collected
    assert process._proc.returncode == 0


def test_an_exception_mid_grace_still_escalates(tmp_path):
    script = make_script(
        tmp_path,
        "interrupted_grace",
        "( trap '' TERM; printf 'descendant:%s\\n' \"$$\"; sleep 30 ) &\nsleep 30\n",
    )
    process = _posix_pty.PosixPtyProcess()
    try:
        process.spawn([script])
        descendant = reported_pid(process, "descendant")

        def interrupting_tick():
            raise KeyboardInterrupt()

        process._grace_tick = interrupting_tick
        with pytest.raises(KeyboardInterrupt):
            process.terminate_tree(grace=1.0)
    finally:
        process.close()

    assert wait_until_gone(descendant)
    assert process._proc.returncode is not None


def test_a_reader_failure_during_teardown_still_escalates(tmp_path):
    """Teardown records the error and runs the sequence to completion before reporting."""
    script = make_script(
        tmp_path,
        "reader_fault_grace",
        "trap '' TERM\n( trap '' TERM; printf 'descendant:%s\\n' \"$$\"; sleep 30 ) &\nsleep 30\n",
    )
    process = _posix_pty.PosixPtyProcess()
    fault = arm_fault(process, "_select")
    try:
        process.spawn([script])
        descendant = reported_pid(process, "descendant")

        real_tick = process._grace_tick

        def failing_tick():
            fault["armed"] = True
            real_tick()

        process._grace_tick = failing_tick
        process.terminate_tree(grace=0.5)
    finally:
        process.close()

    assert wait_until_gone(descendant)
    assert process.reader_failed.is_set()
    with pytest.raises(_posix_pty.TerminalReaderError) as failure:
        process._check_reader_failed()
    assert failure.value.exit_code == 69


def test_a_reader_failure_during_the_handshake_aborts_it_promptly():
    process = _posix_pty.PosixPtyProcess()
    fault = arm_fault(process, "_select")
    fault["armed"] = True
    started = time.monotonic()
    try:
        with pytest.raises(_posix_pty.TerminalReaderError) as failure:
            process.spawn(["/bin/sh", "-c", "sleep 30"], handshake_timeout=SPAWN_TIMEOUT)
    finally:
        process.close()

    assert time.monotonic() - started < SPAWN_TIMEOUT  # not at the deadline
    assert failure.value.exit_code == 69
    assert process._bundle.master_fd is None and process._bundle.wakeup_r is None
    assert process._bundle.err_w is None
    assert process._proc.returncode is not None  # the child was terminated


def test_a_failing_read_closes_the_descriptors_and_is_classified(tmp_path):
    script = make_script(tmp_path, "chatty", "while true; do printf tick; sleep 0.05; done\n")
    process = _posix_pty.PosixPtyProcess()
    fault = arm_fault(process, "_read_master")
    try:
        process.spawn([script])
        wait_for_output(process, "tick")
        fault["armed"] = True
        deadline = time.monotonic() + SPAWN_TIMEOUT
        while not process.reader_failed.is_set() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert process.reader_failed.is_set()
        with pytest.raises(_posix_pty.TerminalReaderError) as failure:
            process._check_reader_failed()
        process.terminate_tree(grace=0.05)
    finally:
        process.close()

    assert failure.value.exit_code == 69
    assert process._bundle.master_fd is None and process._bundle.wakeup_r is None
    assert process._bundle.err_w is None
    assert process._proc.returncode is not None


def test_a_failing_final_flush_is_published_with_err_w_closed_last(tmp_path):
    script = make_script(tmp_path, "brief", "printf bye\nexit 0\n")
    process = _posix_pty.PosixPtyProcess()
    process._flush_decoder = lambda decoder: (_ for _ in ()).throw(RuntimeError("injected flush failure"))
    try:
        process.spawn([script])
        wait_for_output(process, "bye")
        deadline = time.monotonic() + SPAWN_TIMEOUT
        while not process.reader_failed.is_set() and time.monotonic() < deadline:
            time.sleep(0.02)
    finally:
        process.close()

    assert process.reader_failed.is_set()
    assert isinstance(process.reader_exc, RuntimeError)
    assert process._bundle.err_w is None  # closed last, after everything else was released


def test_the_veof_transaction_runs_on_the_reader_and_restores_the_terminal_mode(tmp_path):
    script = make_script(tmp_path, "veof_owner", "sleep 5\n")
    process = _posix_pty.PosixPtyProcess()
    threads = []
    receipts = []
    real_prepare = process._veof_prepare
    real_submit = process._input_queue.submit

    def recording_prepare():
        threads.append(threading.current_thread().name)
        real_prepare()

    def recording_submit(*args, **kwargs):
        result, receipt = real_submit(*args, **kwargs)
        receipts.append(receipt)
        return result, receipt

    process._veof_prepare = recording_prepare
    process._input_queue.submit = recording_submit
    try:
        process.spawn([script])
        attributes = termios.tcgetattr(process._bundle.master_fd)  # read-only probe
    finally:
        process.terminate_tree(grace=0.05)
        process.close()

    assert threads == ["codeplain-pty-reader"], "no parent helper may touch the raw master"
    assert receipts and receipts[0].resolutions == 1
    assert attributes[3] & termios.ECHO, "the snapshot was not restored"


def test_a_failing_veof_snapshot_prevents_the_ack():
    process = _posix_pty.PosixPtyProcess()
    process._veof_prepare = lambda: (_ for _ in ()).throw(OSError(errno.EIO, "injected snapshot failure"))
    try:
        with pytest.raises(TerminalEnvironmentError) as failure:
            process.spawn(["/bin/sh", "-c", "printf ran"])
    finally:
        process.close()

    assert failure.value.exit_code == 69
    assert not process._acked
    assert process.read_raw_output() == b""  # the target never ran


def test_a_failing_veof_restore_is_attempted_and_reported():
    process = _posix_pty.PosixPtyProcess()
    attempts = []

    def failing_restore():
        attempts.append("restore")
        raise OSError(errno.EIO, "injected restore failure")

    process._veof_restore = failing_restore
    try:
        with pytest.raises(TerminalEnvironmentError) as failure:
            process.spawn(["/bin/sh", "-c", "printf ran"])
    finally:
        process.close()

    assert attempts == ["restore"]  # every path that changed the mode attempts the restore
    assert failure.value.exit_code == 69
    assert not process._acked


def test_the_veof_survives_an_eagain_mid_item(tmp_path):
    script = make_script(tmp_path, "veof_eagain", "read line\nprintf 'read-returned:%s\\n' \"$?\"\n")
    process = _posix_pty.PosixPtyProcess()
    real_write = process._write_master
    state = {"blocked": False}

    def blocking_once(fd, data):
        if not state["blocked"]:
            state["blocked"] = True
            raise BlockingIOError(errno.EAGAIN, "injected EAGAIN")
        return real_write(fd, data)

    process._write_master = blocking_once
    try:
        process.spawn([script])
        wait_for_output(process, "read-returned:")
        assert wait_for_exit(process) == 0
    finally:
        process.close()

    assert state["blocked"]


def test_close_during_an_in_flight_fragmented_item_fails_its_receipt_once(tmp_path):
    script = make_script(tmp_path, "fragmented_close", "sleep 10\n")
    process = _posix_pty.PosixPtyProcess()
    try:
        process.spawn([script], input_driver=object())
        real_write = process._write_master
        state = {"calls": 0}

        def stalling_write(fd, data):
            state["calls"] += 1
            if state["calls"] == 1:
                return real_write(fd, data[:1])
            raise BlockingIOError(errno.EAGAIN, "held mid-item")

        process._write_master = stalling_write
        payload = b"abcdef"
        result, receipt = process._input_queue.submit(payload)
        assert result.disposition is InputDisposition.ACCEPTED
        process._ring_doorbell()

        deadline = time.monotonic() + SPAWN_TIMEOUT
        while state["calls"] < 2 and time.monotonic() < deadline:
            time.sleep(0.02)
        assert state["calls"] >= 2
        # Dequeue is not completion: the retained cursor still counts against the cap.
        assert process._input_queue.pending_bytes() == len(payload)

        process.close()
    finally:
        process.terminate_tree(grace=0.05)
        process.close()

    assert receipt.resolutions == 1
    assert receipt.disposition is InputDisposition.CLOSED
    assert process._input_queue.pending_bytes() == 0


def test_close_finishes_an_in_flight_compound_item_before_failing_its_receipt(tmp_path):
    """An EAGAIN'd echo-suppressed item still has its transaction closed by teardown.

    Without that, a close during the spawn-time VEOF publishes CLOSED with the terminal
    left in the mode `prepare` put it in.
    """
    script = make_script(tmp_path, "compound_close", "sleep 10\n")
    process = _posix_pty.PosixPtyProcess()
    prepared = threading.Event()
    finished = []

    def prepare():
        process._veof_prepare()
        prepared.set()

    def finish():
        process._veof_restore()
        finished.append(termios.tcgetattr(process._bundle.master_fd))  # the reader still owns it

    try:
        process.spawn([script], input_driver=object())

        def held_write(fd, data):
            raise BlockingIOError(errno.EAGAIN, "held mid-item")

        process._write_master = held_write
        result, receipt = process._input_queue.submit(b"\x04", reserved=True, prepare=prepare, finish=finish)
        assert result.disposition is InputDisposition.ACCEPTED
        process._ring_doorbell()

        assert prepared.wait(SPAWN_TIMEOUT)
        assert not termios.tcgetattr(process._bundle.master_fd)[3] & termios.ECHO
        process.close()
    finally:
        process.terminate_tree(grace=0.05)
        process.close()

    assert len(finished) == 1, "the in-flight transaction was never closed"
    assert finished[0][3] & termios.ECHO, "the terminal mode was not restored"
    assert receipt.resolutions == 1
    assert receipt.disposition is InputDisposition.CLOSED
    assert process._input_queue.pending_bytes() == 0


def test_a_saturated_doorbell_is_only_a_coalesced_notification(tmp_path):
    script = make_script(tmp_path, "doorbell", "sleep 10\n")
    process = _posix_pty.PosixPtyProcess()
    try:
        process.spawn([script], input_driver=object())
        while True:  # fill the doorbell to EAGAIN
            try:
                os.write(process._wakeup_w, b"\x01" * 4096)
            except BlockingIOError:
                break

        result = process.write_input(b"after saturation\n")
        assert result.disposition is InputDisposition.ACCEPTED

        started = time.monotonic()
        process.close()
        elapsed = time.monotonic() - started
    finally:
        process.terminate_tree(grace=0.05)
        process.close()

    assert elapsed < _posix_pty.DRAIN_DEADLINE_SECONDS + SHORT_TIMEOUT
    assert process._input_queue.pending_bytes() == 0


def test_a_fragmented_logical_write_keeps_its_suffix_ahead_of_later_items(tmp_path):
    """The public result stays whole-item; no PARTIAL and no interleaving escape."""
    script = make_script(tmp_path, "ordering", "sleep 10\n")
    process = _posix_pty.PosixPtyProcess()
    written = []
    released = threading.Event()
    try:
        process.spawn([script], input_driver=object())
        real_write = process._write_master
        state = {"held": False}

        def fragmenting_write(fd, data):
            count = real_write(fd, data[:2])
            written.append(data[:count])
            if not state["held"]:
                state["held"] = True
                released.wait(SHORT_TIMEOUT)  # hold the reader inside the first item
            return count

        process._write_master = fragmenting_write
        first = process.write_input(b"AAAAAAAA")
        deadline = time.monotonic() + SPAWN_TIMEOUT
        while not state["held"] and time.monotonic() < deadline:
            time.sleep(0.02)
        second = process.write_input(b"BBBB")
        third = process.write_input(b"CCCC")
        released.set()

        deadline = time.monotonic() + SPAWN_TIMEOUT
        while process._input_queue.has_pending() and time.monotonic() < deadline:
            time.sleep(0.02)
    finally:
        released.set()
        process.terminate_tree(grace=0.05)
        process.close()

    assert [r.disposition for r in (first, second, third)] == [InputDisposition.ACCEPTED] * 3
    assert [r.accepted_bytes for r in (first, second, third)] == [8, 4, 4]
    assert b"".join(written) == b"AAAAAAAABBBBCCCC"


def test_the_final_drain_is_bounded_against_a_continuously_writing_escapee(tmp_path):
    escapee = tmp_path / "escapee.py"
    escapee.write_text(
        "import os, sys, time\n"
        "os.setpgid(0, 0)\n"
        "sys.stdout.write('escapee:%d\\n' % os.getpid())\n"
        "sys.stdout.flush()\n"
        "end = time.monotonic() + 30\n"
        "while time.monotonic() < end:\n"
        "    sys.stdout.write('x' * 4096)\n"
        "    sys.stdout.flush()\n"
    )
    script = make_script(tmp_path, "escaper", f'"{sys.executable}" "{escapee}" &\nsleep 30\n')

    process = _posix_pty.PosixPtyProcess()
    escapee_pid = None
    try:
        process.spawn([script])
        escapee_pid = reported_pid(process, "escapee")
        process.terminate_tree(grace=0.1)  # the escapee left the group and survives
        started = time.monotonic()
        process.close()
        elapsed = time.monotonic() - started
        decoded = process.read_output()
        raw = process.read_raw_output()
    finally:
        process.close()
        if escapee_pid is not None and not wait_until_gone(escapee_pid, timeout=0.5):
            os.kill(escapee_pid, signal.SIGKILL)

    assert elapsed < _posix_pty.DRAIN_DEADLINE_SECONDS + SHORT_TIMEOUT
    assert "x" in decoded, "what the drain retained must reach the decoded channel too"
    assert b"x" in raw


def test_output_in_flight_at_close_reaches_the_decoded_channel(tmp_path):
    """The reader is parked, so the marker can only be picked up by the final drain."""
    script = make_script(tmp_path, "inflight", "printf 'inflight-marker\\n'\nsleep 10\n")
    process = _posix_pty.PosixPtyProcess()
    parked = {"calls": 0}

    def parked_read_once(master_fd, decoder):
        parked["calls"] += 1  # the master is readable, but the bytes stay in the terminal
        time.sleep(0.02)
        return True

    process._read_once = parked_read_once
    try:
        process.spawn([script], input_driver=object())
        deadline = time.monotonic() + SPAWN_TIMEOUT
        while parked["calls"] < 2 and time.monotonic() < deadline:
            time.sleep(0.02)
        assert parked["calls"] >= 2, "the target never wrote anything"
        process.close()
        decoded = process.read_output()
        raw = process.read_raw_output()
    finally:
        process.terminate_tree(grace=0.05)
        process.close()

    assert "inflight-marker" in decoded
    assert b"inflight-marker" in raw


def test_write_input_after_the_reader_closed_the_master_touches_nothing(tmp_path):
    unrelated_path = tmp_path / "unrelated.txt"
    process = _posix_pty.PosixPtyProcess()
    unrelated = None
    try:
        process.spawn(["/bin/sh", "-c", "printf bye"], input_driver=object())
        assert wait_for_exit(process) == 0
        deadline = time.monotonic() + SPAWN_TIMEOUT
        while process._bundle.master_fd is not None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert process._bundle.master_fd is None
        process.close()

        unrelated = os.open(str(unrelated_path), os.O_CREAT | os.O_RDWR, 0o600)
        result = process.write_input(b"nowhere")
        os.write(unrelated, b"untouched")
    finally:
        if unrelated is not None:
            os.close(unrelated)
        process.close()

    assert result.disposition is InputDisposition.CLOSED
    assert result.accepted_bytes == 0
    assert unrelated_path.read_bytes() == b"untouched"


def test_a_jumping_wall_clock_changes_nothing(monkeypatch, tmp_path):
    """Every budget is monotonic; wall time is only ever a human timestamp."""
    assert "time.time(" not in Path(_posix_pty.__file__).read_text()

    jumps = iter([10_000.0, -10_000.0])
    real_time = time.time

    def jumping_time():
        try:
            return real_time() + next(jumps)
        except StopIteration:
            return real_time()

    monkeypatch.setattr(time, "time", jumping_time)
    script = make_script(tmp_path, "clock", "printf steady\nsleep 30\n")
    started = time.monotonic()
    process = _posix_pty.PosixPtyProcess()
    try:
        process.spawn([script])
        wait_for_output(process, "steady")
        assert process.poll() is None  # not terminated early
        process.terminate_tree(grace=0.2)
    finally:
        process.close()

    assert time.monotonic() - started < SPAWN_TIMEOUT
    assert process._proc.returncode is not None


def test_the_interpreter_exits_while_the_reader_and_the_reaper_are_still_blocked(tmp_path):
    """Every thread this design starts is a daemon; a non-daemon one hangs shutdown.

    The target outlives the outer bound by a wide margin, so the reader is still blocked
    on the master when the bound expires: only daemon threads let the interpreter exit
    inside it.
    """
    driver = tmp_path / "daemon_threads.py"
    driver.write_text(
        "import subprocess, sys, threading\n"
        f"sys.path.insert(0, {str(REPO_ROOT)!r})\n"
        "from render_machine import _posix_pty\n"
        "never_set = threading.Event()\n"
        "class StuckProc:\n"
        "    pid = -1\n"
        "    def wait(self, timeout=None):\n"
        "        if timeout is not None:\n"
        "            raise subprocess.TimeoutExpired('stuck', timeout)\n"
        "        never_set.wait()\n"
        "process = _posix_pty.PosixPtyProcess()\n"
        "process.spawn(['/bin/sh', '-c', 'sleep 300'])\n"
        "_posix_pty._reap(StuckProc(), 0.01)\n"
        "assert process._reader.is_alive()\n"
        "sys.stdout.write('ready:%d\\n' % process._pgid)\n"
        "sys.stdout.flush()\n"
    )
    started = time.monotonic()
    completed = subprocess.run([sys.executable, str(driver)], capture_output=True, text=True, timeout=SPAWN_TIMEOUT)
    elapsed = time.monotonic() - started

    assert "ready:" in completed.stdout, completed.stderr
    target_pgid = int(completed.stdout.split("ready:", 1)[1].split()[0])
    try:
        assert completed.returncode == 0
        assert elapsed < SPAWN_TIMEOUT
    finally:  # the driver exits without terminating its target
        try:
            os.killpg(target_pgid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass


def framed(kind, payload=b""):
    return bytes([kind]) + len(payload).to_bytes(4, "big") + payload


def test_the_handshake_parser_accepts_only_started_then_session_ready_then_eof():
    parser = _posix_pty._HandshakeParser()
    parser.feed(framed(pty_exec.STARTED))
    parser.feed(framed(pty_exec.SESSION_READY))
    assert parser.session_ready
    parser.eof()  # the only success case


@pytest.mark.parametrize(
    "chunks",
    [
        [bytes([0x7F]) + (0).to_bytes(4, "big")],  # unknown record type
        [bytes([pty_exec.STARTED]) + (pty_exec.MAX_PAYLOAD + 1).to_bytes(4, "big")],  # oversized length
        [framed(pty_exec.STARTED, b"payload")],  # a marker carrying a payload
        [framed(pty_exec.SESSION_READY)],  # SESSION_READY before STARTED
        [framed(pty_exec.STARTED), framed(pty_exec.STARTED)],  # duplicate marker
        [framed(pty_exec.STARTED), framed(pty_exec.SESSION_READY), framed(pty_exec.SESSION_READY)],
        [framed(pty_exec.STARTED), framed(pty_exec.FAILED, b"boom"), b"trailing"],
    ],
)
def test_the_handshake_parser_rejects_malformed_frames(chunks):
    """An oversized length is rejected before its body is allocated or waited for."""
    parser = _posix_pty._HandshakeParser()
    with pytest.raises(_posix_pty._ProtocolError):
        for chunk in chunks:
            parser.feed(chunk)


@pytest.mark.parametrize(
    "chunks",
    [
        [framed(pty_exec.STARTED), b"\x02\x00"],  # truncated header at EOF
        [framed(pty_exec.STARTED), bytes([pty_exec.FAILED]) + (8).to_bytes(4, "big") + b"half"],
        [framed(pty_exec.STARTED)],  # EOF after only STARTED
        [],  # EOF with no marker at all
    ],
)
def test_the_handshake_parser_rejects_incomplete_streams_at_eof(chunks):
    parser = _posix_pty._HandshakeParser()
    for chunk in chunks:
        parser.feed(chunk)
    with pytest.raises(_posix_pty._ProtocolError):
        parser.eof()


def test_the_handshake_parser_reassembles_fragmented_records():
    parser = _posix_pty._HandshakeParser()
    stream = framed(pty_exec.STARTED) + framed(pty_exec.SESSION_READY)
    for index in range(len(stream)):
        parser.feed(stream[index : index + 1])
    assert parser.started and parser.session_ready
    parser.eof()
