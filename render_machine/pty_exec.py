"""Launcher that gives a command its own terminal and then becomes that command.

Spawned by the POSIX backend as ``python -I -S pty_exec.py <slave_fd> <status_fd>
<ack_fd> -- <command...>``, it attaches the PTY slave to fds 0, 1 and 2, verifies the
terminal invariants that only a pre-exec child can verify, reports progress to the
parent over a framed status pipe, waits for the parent's acknowledgment, and execs.

The acknowledgment is a barrier: the parent records the process group before releasing
the target, so the target cannot create a descendant the parent does not know how to
terminate. The proof holds only while nothing but this file runs before the ack, which
is what ``-I -S`` guarantees — hence the deliberately tiny import set (os, sys, select,
signal, all builtin C modules) and the absolute-path spawn.
"""

import os
import select
import signal
import sys

if sys.platform == "win32":  # pragma: no cover - the launcher is POSIX-only
    raise ImportError("render_machine.pty_exec is POSIX-only")

STARTED = 0x01  # record type: the interpreter reached our code
SESSION_READY = 0x02  # record type: setsid done, pgid == pid; parent may record it
FAILED = 0x03  # record type: payload is the framed error text

HEADER_SIZE = 5  # one type byte + 4-byte big-endian length
MAX_PAYLOAD = 8192  # bound the error text so a record can never be unbounded

LAUNCH_FAILURE_EXIT_CODE = 127

# Backstop against a parent that is alive but never acknowledges — a bug, not an
# operating condition. It sits comfortably above the parent's own handshake bound so
# the parent's deadline expires first in every realistic failure, leaving one deadline
# owner and one diagnostic path. Tests lower it through the environment to drive the
# launcher-times-out-first boundary deterministically.
ACK_TIMEOUT = 60.0
ACK_TIMEOUT_ENV = "CODEPLAIN_PTY_ACK_TIMEOUT"

# The set Popen(restore_signals=True) resets. CPython sets SIGPIPE to SIG_IGN at
# startup and an ignored disposition survives execvpe, so without this the target
# would inherit an ignored SIGPIPE where the pipe backend delivers the default.
RESTORED_SIGNALS = ("SIGPIPE", "SIGXFZ", "SIGXFSZ")


def _write_record(fd: int, kind: int, payload: bytes = b"") -> None:
    """One type byte + 4-byte big-endian length + payload. Never a bare marker."""
    payload = payload[:MAX_PAYLOAD]
    buf = bytes([kind]) + len(payload).to_bytes(4, "big") + payload
    while buf:  # os.write may write fewer bytes than asked; a short write would
        buf = buf[os.write(fd, buf) :]  # leave a valid header followed by a truncated payload


def _ack_timeout() -> float:
    raw = os.environ.get(ACK_TIMEOUT_ENV)
    if not raw:
        return ACK_TIMEOUT
    try:
        return float(raw)
    except ValueError:
        return ACK_TIMEOUT


def _await_ack(ack_fd: int, timeout: float) -> None:
    """Blocks until the parent acknowledges. EOF or timeout is a launch failure.

    The parent holds the only write end, so a dead parent surfaces as an immediate EOF
    rather than as a wait for the timeout.
    """
    readable, _, _ = select.select([ack_fd], [], [], timeout)
    if not readable:
        raise RuntimeError(f"parent did not acknowledge within {timeout} seconds")
    if not os.read(ack_fd, 1):
        raise RuntimeError("parent closed the acknowledgment pipe without acknowledging")


def _assert_invariants() -> None:
    pid = os.getpid()
    if not (os.isatty(0) and os.isatty(1) and os.isatty(2)):
        raise RuntimeError("PTY is not attached to all three descriptors")
    if os.getsid(0) != pid or os.getpgrp() != pid:
        raise RuntimeError("login_tty did not make this process session and group leader")
    if os.tcgetpgrp(0) != os.getpgrp():
        raise RuntimeError("PTY foreground process group is not this process")
    tty_fd = os.open("/dev/tty", os.O_RDWR | getattr(os, "O_CLOEXEC", 0))
    try:  # proves a controlling terminal exists, not merely that fd 0
        if not os.isatty(tty_fd):  # happens to name some terminal device
            raise RuntimeError("/dev/tty is not a terminal")
    finally:
        os.close(tty_fd)


def _restore_signals() -> None:
    for name in RESTORED_SIGNALS:
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), signal.SIG_DFL)


def _format_launch_error(exc: BaseException) -> bytes:
    return f"{type(exc).__name__}: {exc}".encode("utf-8", "replace")


def main(slave_fd: int, status_fd: int, ack_fd: int, command: list) -> None:
    try:
        _write_record(status_fd, STARTED)  # before anything that can fail
        os.login_tty(slave_fd)  # setsid + TIOCSCTTY + dup2 onto 0,1,2 + close slave_fd
        if os.tcgetpgrp(0) != os.getpgrp():
            os.tcsetpgrp(0, os.getpgrp())
        _assert_invariants()  # in the child, pre-exec — the parent cannot do this
        _write_record(status_fd, SESSION_READY)
        _await_ack(ack_fd, _ack_timeout())  # barrier: the target must not run before the parent records pgid
        os.set_inheritable(status_fd, False)  # successful exec closes it -> parent sees EOF
        os.set_inheritable(ack_fd, False)
        _restore_signals()  # SIG_IGN survives exec
        os.environ.pop(ACK_TIMEOUT_ENV, None)  # a test hook never reaches the target
        os.execvpe(command[0], command, os.environ)
    except BaseException as exc:
        try:
            _write_record(status_fd, FAILED, _format_launch_error(exc))
        except BaseException:
            pass  # the parent falls back to EOF-without-marker plus the stderr pipe
        os._exit(LAUNCH_FAILURE_EXIT_CODE)


def _run(argv: list) -> None:
    slave_fd, status_fd, ack_fd = (int(argv[0]), int(argv[1]), int(argv[2]))
    if argv[3] != "--":
        raise ValueError(f"expected '--' before the command, got {argv[3]!r}")
    main(slave_fd, status_fd, ack_fd, argv[4:])


if __name__ == "__main__":
    try:
        _run(sys.argv[1:])
    except BaseException:  # argv is malformed, so there is no status fd to report on
        os._exit(LAUNCH_FAILURE_EXIT_CODE)
