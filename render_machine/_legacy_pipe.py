"""Legacy pipe backend for `TerminalProcess`.

Wraps the `Popen(stdout=PIPE, stderr=STDOUT, start_new_session=True)` path Codeplain
shipped before the PTY, behind the same interface. It survives for one reason: it is the
`CODEPLAIN_NO_PTY` escape hatch, on POSIX and on Windows alike. Neither platform selects
it automatically.

The child's stdin is `DEVNULL`, permanently and on every platform. A child without a
terminal of its own would otherwise inherit Codeplain's fd 0, and `start_new_session=True`
removes the controlling terminal whose absence makes the kernel permit the read instead of
stopping it — so the child would consume the user's keystrokes. Closing that hole is the
one thing this backend may never give back.

On Windows the same hole needs a second lock, because redirecting the standard input
handle does not stop a child from opening `CONIN$`: that opens the console input buffer of
whatever console the child is attached to, which by default is the renderer's own. Only
detaching the child from that console closes it, so every Windows child is created with
`CREATE_NO_WINDOW`.

There is no input channel, so `write_input()` accepts nothing and a query the target
prints is rendered without a reply: a backend that cannot answer must not register an
obligation it can only fail.
"""

import codecs
import os
import signal
import subprocess
import sys
import threading
import time
from typing import Optional, Sequence, Tuple

from plain2code_console import console
from plain2code_exceptions import RenderCancelledError
from render_machine.output_normalizer import OutputNormalizer
from render_machine.terminal_process import (
    DRAIN_DEADLINE_SECONDS,
    GRACE_TICK_SECONDS,
    READ_CHUNK_BYTES,
    REAP_DEADLINE_SECONDS,
    SIGTERM_GRACE_PERIOD_SECONDS,
    TERMINAL_COLUMNS,
    TERMINAL_ROWS,
    InputDisposition,
    InputWriteResult,
    TerminalLaunchError,
    TerminalProcess,
    child_environment,
)
from render_machine.terminal_queries import TerminalQueryResponder

if sys.platform == "linux":
    import fcntl

F_SETPIPE_SIZE = 1031  # Linux-only constant
PIPE_SIZE_KB = 1024  # 1MB

# How long close() waits for the reader before it closes the pipe under it. A descendant
# that inherited the write end keeps the pipe open past the leader's exit.
CLOSE_JOIN_SECONDS = 1.0

# What one full teardown of this backend may spend, phase by phase and in sequence. A
# caller waiting on a render derives its own bound from this, so it cannot report a stuck
# teardown while the backend is still inside the budget its own constants grant it.
TEARDOWN_BUDGET_SECONDS = (
    SIGTERM_GRACE_PERIOD_SECONDS  # terminate_tree(): the grace before the SIGKILL
    + REAP_DEADLINE_SECONDS  # terminate_tree(): reaping the killed process
    + DRAIN_DEADLINE_SECONDS  # close(): the first join, while the pipe is still open
    + CLOSE_JOIN_SECONDS  # close(): the second, after the pipe is closed under the reader
)

# Windows gives a child the parent's console unless told otherwise, and a child on that
# console can read the renderer's keystrokes through CONIN$ regardless of where its
# standard input handle points. CREATE_NO_WINDOW gives it a console of its own instead.
if sys.platform == "win32":
    CREATION_FLAGS = subprocess.CREATE_NO_WINDOW
else:
    CREATION_FLAGS = 0


class LegacyPipeProcess(TerminalProcess):
    """One command, one pipe carrying its merged stdout and stderr, one reader thread."""

    def __init__(self) -> None:
        super().__init__()
        # No admission callable: the responder starts QUIESCED, so an escape sequence the
        # target prints is rendered and nothing is ever owed to it.
        self.query_responder = TerminalQueryResponder()
        # The parser still reports the queries it sees, so they are accounted for on the
        # render-only side rather than silently dropped. A pipe has no line discipline to
        # apply ONLCR, so the normalizer performs that translation itself — without it a
        # stream of bare linefeeds renders as a whitespace staircase.
        self.normalizer = OutputNormalizer(reply_handler=self.query_responder.answer, translate_newlines=True)

        self._proc: Optional[subprocess.Popen] = None
        self._reader: Optional[threading.Thread] = None
        self._spawned = False
        self._closed = False
        self._closing = threading.Event()
        self._reaped = False
        self._stdout_redirected = False

    # ---------------------------------------------------------------- public API

    def spawn(
        self,
        command: Sequence[str],
        cwd: Optional[str] = None,
        env: Optional[dict] = None,
        terminal_size: Tuple[int, int] = (TERMINAL_COLUMNS, TERMINAL_ROWS),
        stop_event: Optional[threading.Event] = None,
    ) -> None:
        if self._spawned:
            raise RuntimeError("LegacyPipeProcess instances are single-use")
        self._spawned = True
        if stop_event is not None and stop_event.is_set():
            # A cancellation already observed must not start the target: the script would
            # run its side effects before the wait loop could notice.
            raise RenderCancelledError()
        columns, rows = terminal_size
        self.normalizer.resize(columns, rows)
        try:
            self._proc = subprocess.Popen(
                list(command),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                cwd=cwd,
                env=child_environment(env),
                start_new_session=(sys.platform != "win32"),
                creationflags=CREATION_FLAGS,
            )
        except OSError as exc:
            raise TerminalLaunchError(f"Could not start the script: {exc}") from exc
        self._widen_pipe()
        # Drain in a background thread: without continuous draining a script that
        # outproduces the pipe buffer blocks on write and never exits.
        self._reader = threading.Thread(target=self._reader_main, name="codeplain-pipe-reader", daemon=True)
        self._reader.start()

    def poll(self) -> Optional[int]:
        if self._proc is None:
            return None
        returncode = self._proc.poll()
        if returncode is not None:
            self._reaped = True
        return returncode

    def write_input(self, data: bytes) -> InputWriteResult:
        """Always closed: this backend hands the child `DEVNULL`, by design."""
        return InputWriteResult(InputDisposition.CLOSED, 0)

    def resize(self, columns: int, rows: int) -> None:
        """There is no terminal to resize; only the rendering parser follows the size."""
        self.normalizer.resize(columns, rows)

    def terminate_tree(self, grace: float = SIGTERM_GRACE_PERIOD_SECONDS) -> None:
        proc = self._proc
        if proc is None or self._reaped:
            return
        if sys.platform == "win32":
            self._terminate_windows(proc, grace)
            return
        # The leader is deliberately left unreaped until after the escalation: a zombie
        # leader pins the group id against reuse, so the SIGKILL below can never reach a
        # recycled group. The grace therefore watches the whole group, not the leader —
        # members that outlive an instantly-dying leader get their grace too, and a group
        # that empties within it is never SIGKILLed at all.
        escalate = True
        try:
            try:
                self._signal(proc, terminal=False)
                deadline = time.monotonic() + grace
                while time.monotonic() < deadline:
                    if self._group_spent(proc.pid):
                        escalate = False
                        break
                    time.sleep(GRACE_TICK_SECONDS)
            finally:
                if escalate:
                    self._signal(proc, terminal=True)
        finally:
            # Reaped in a finally of its own: an exception escaping the grace loop has
            # already escalated above, and skipping the reap here would leave a zombie
            # whose eventual collection unpins the group id mid-retry.
            try:
                proc.wait(timeout=REAP_DEADLINE_SECONDS)
            except subprocess.TimeoutExpired:
                console.debug(f"process {proc.pid} outlived the reap deadline")
            else:
                self._reaped = True

    def _terminate_windows(self, proc: subprocess.Popen, grace: float) -> None:
        """TerminateProcess is already terminal, so there is nothing to escalate to."""
        self._signal_process(proc, terminal=False)
        try:
            proc.wait(timeout=grace + REAP_DEADLINE_SECONDS)
        except subprocess.TimeoutExpired:
            console.debug(f"process {proc.pid} outlived the reap deadline")
            return
        self._reaped = True

    def _group_spent(self, pgid: int) -> bool:
        """True once the group has no live member left to signal.

        macOS reports a group whose remaining members are all zombies as EPERM rather
        than ESRCH; both mean the grace has done its work.
        """
        try:
            os.killpg(pgid, 0)
        except (ProcessLookupError, PermissionError):
            return True
        except OSError:
            return False
        return False

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._closing.set()  # from here a failing read is expected closure, not a fault
        stalled = False
        if self._reader is not None and self._reader.ident is not None:
            self._reader.join(timeout=DRAIN_DEADLINE_SECONDS)
            if self._reader.is_alive():
                # A descendant is holding the write end open. The read end is redirected to
                # devnull so any read that returns sees end-of-file; a read the kernel keeps
                # parked past the second join is published as a stall below.
                self._close_stdout()
                self._reader.join(timeout=CLOSE_JOIN_SECONDS)
            stalled = self._reader.is_alive()
        self._close_stdout()
        self.normalizer.finalize()
        if stalled:
            self._publish_reader_stall()

    # -------------------------------------------------------------------- internals

    def _widen_pipe(self) -> None:
        """Best-effort 1MB pipe buffer, so bursts of output need fewer reader wakeups."""
        if sys.platform == "linux":
            assert self._proc is not None and self._proc.stdout is not None
            try:
                fcntl.fcntl(self._proc.stdout.fileno(), F_SETPIPE_SIZE, PIPE_SIZE_KB * 1024)
            except OSError as exc:  # a lowered fs.pipe-max-size is not a launch failure
                console.debug(f"could not widen the output pipe: {exc}")

    def _signal(self, proc: subprocess.Popen, terminal: bool) -> None:
        """Signals the child's whole group, falling back to the child alone.

        `start_new_session` makes the child its own group leader, so the group id is the
        child's pid itself — resolvable even after the leader has exited and been reaped,
        which `os.getpgid()` on the pid no longer is.
        """
        if sys.platform == "win32":
            self._signal_process(proc, terminal)
            return
        try:
            os.killpg(proc.pid, signal.SIGKILL if terminal else signal.SIGTERM)
        except OSError:
            self._signal_process(proc, terminal)

    def _signal_process(self, proc: subprocess.Popen, terminal: bool) -> None:
        if terminal:
            proc.kill()
        else:
            proc.terminate()

    def _close_stdout(self) -> None:
        """Releases the read end without touching the buffered stream's lock.

        `BufferedReader.close()` takes the same internal lock the reader thread holds while
        parked inside `read1()`, so a foreground close would deadlock exactly when the pipe
        has to be broken. A raw `os.close()` would free the fd number for reuse under that
        parked read instead. `os.dup2()` of devnull replaces the descriptor atomically: it
        never blocks, never recycles the number, and a reader that wakes later reads
        end-of-file. The buffered object itself is only closed once the reader is gone.
        """
        proc = self._proc
        if proc is None or proc.stdout is None:
            return
        stream = proc.stdout
        if not self._stdout_redirected:
            self._stdout_redirected = True
            try:
                devnull = os.open(os.devnull, os.O_RDONLY)
            except OSError:
                devnull = -1
            if devnull >= 0:
                try:
                    os.dup2(devnull, stream.fileno())
                except (OSError, ValueError):
                    pass
                finally:
                    os.close(devnull)
        if (self._reader is None or not self._reader.is_alive()) and not stream.closed:
            try:
                stream.close()
            except OSError:
                pass

    def _reader_main(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        stream = self._proc.stdout
        reader_exc: Optional[BaseException] = None
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        try:
            while True:
                chunk = stream.read1(READ_CHUNK_BYTES)
                if not chunk:
                    break
                self._feed_output(chunk, decoder)
        except (OSError, ValueError) as exc:
            # Expected only once the pipe is gone: close() breaks a parked read by closing
            # it underneath the reader. The same error while the backend is still active
            # is an independent reader failure and has to be published like any other.
            if not (self._closing.is_set() or stream.closed):
                reader_exc = exc
        except BaseException as exc:  # nothing here reaches threading.excepthook
            reader_exc = exc
        finally:
            try:
                self._flush_decoder(decoder)
                self.normalizer.finalize()
            except BaseException as exc:
                reader_exc = reader_exc or exc
            self.reader_exc = reader_exc  # stored while still unobservable
            if reader_exc is not None:
                self.reader_failed.set()
