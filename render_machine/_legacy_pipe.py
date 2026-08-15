"""Legacy pipe backend for `TerminalProcess`.

Wraps the `Popen(stdout=PIPE, stderr=STDOUT, start_new_session=True)` path Codeplain
shipped before the PTY, behind the same interface. It survives for two reasons: it is the
`CODEPLAIN_NO_PTY` escape hatch, and it is the Windows interim until the ConPTY backend
lands.

The child's stdin is `DEVNULL`, permanently and on every platform. A child without a
terminal of its own would otherwise inherit Codeplain's fd 0, and `start_new_session=True`
removes the controlling terminal whose absence makes the kernel permit the read instead of
stopping it — so the child would consume the user's keystrokes. Closing that hole is the
one thing this backend may never give back.

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
from typing import List, Optional, Sequence, Tuple

from plain2code_console import console
from render_machine.output_normalizer import OutputNormalizer
from render_machine.terminal_process import (
    DRAIN_DEADLINE_SECONDS,
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


class LegacyPipeProcess(TerminalProcess):
    """One command, one pipe carrying its merged stdout and stderr, one reader thread."""

    def __init__(self) -> None:
        self.reader_failed = threading.Event()
        self.reader_exc: Optional[BaseException] = None
        # No admission callable: the responder starts QUIESCED, so an escape sequence the
        # target prints is rendered and nothing is ever owed to it.
        self.query_responder = TerminalQueryResponder()
        # The parser still reports the queries it sees, so they are accounted for on the
        # render-only side rather than silently dropped.
        self.normalizer = OutputNormalizer(reply_handler=self.query_responder.answer)

        self._proc: Optional[subprocess.Popen] = None
        self._reader: Optional[threading.Thread] = None
        self._spawned = False
        self._closed = False
        self._reaped = False

        self._output_lock = threading.Lock()
        self._decoded: List[str] = []
        self._raw = bytearray()

    # ---------------------------------------------------------------- public API

    def spawn(
        self,
        command: Sequence[str],
        cwd: Optional[str] = None,
        env: Optional[dict] = None,
        terminal_size: Tuple[int, int] = (TERMINAL_COLUMNS, TERMINAL_ROWS),
        stop_event: Optional[threading.Event] = None,
        input_driver: Optional[object] = None,
    ) -> None:
        if self._spawned:
            raise RuntimeError("LegacyPipeProcess instances are single-use")
        self._spawned = True
        columns, rows = terminal_size
        self.normalizer.resize(columns, rows)
        try:
            self._proc = subprocess.Popen(
                list(command),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                cwd=cwd,
                env=self._child_env(env),
                start_new_session=(sys.platform != "win32"),
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

    def read_output(self) -> str:
        with self._output_lock:
            text = "".join(self._decoded)
            self._decoded.clear()
            return text

    def read_raw_output(self) -> bytes:
        with self._output_lock:
            data = bytes(self._raw)
            self._raw.clear()
            return data

    def normalized_output(self) -> str:
        return self.normalizer.text()

    @property
    def terminal_reply_failed(self) -> bool:
        return self.query_responder.reply_failed

    def terminal_reply_detail(self) -> str:
        return self.query_responder.failure_detail()

    def write_input(self, data: bytes) -> InputWriteResult:
        """Always closed: this backend hands the child `DEVNULL`, by design."""
        return InputWriteResult(InputDisposition.CLOSED, 0)

    def terminate_tree(self, grace: float = SIGTERM_GRACE_PERIOD_SECONDS) -> None:
        proc = self._proc
        if proc is None or self._reaped:
            return
        self._signal(proc, terminal=False)
        try:
            proc.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            self._signal(proc, terminal=True)
            try:
                proc.wait(timeout=REAP_DEADLINE_SECONDS)
            except subprocess.TimeoutExpired:
                console.debug(f"process {proc.pid} outlived the reap deadline")
                return
        self._reaped = True

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._reader is not None and self._reader.ident is not None:
            self._reader.join(timeout=DRAIN_DEADLINE_SECONDS)
            if self._reader.is_alive():
                # A descendant is holding the write end open; the parked read has to be
                # broken rather than waited out.
                self._close_stdout()
                self._reader.join(timeout=CLOSE_JOIN_SECONDS)
        self._close_stdout()
        self.normalizer.finalize()

    # -------------------------------------------------------------------- internals

    def _child_env(self, env: Optional[dict]) -> dict:
        return child_environment(env)

    def _widen_pipe(self) -> None:
        """Best-effort 1MB pipe buffer, so bursts of output need fewer reader wakeups."""
        if sys.platform == "linux":
            assert self._proc is not None and self._proc.stdout is not None
            try:
                fcntl.fcntl(self._proc.stdout.fileno(), F_SETPIPE_SIZE, PIPE_SIZE_KB * 1024)
            except OSError as exc:  # a lowered fs.pipe-max-size is not a launch failure
                console.debug(f"could not widen the output pipe: {exc}")

    def _signal(self, proc: subprocess.Popen, terminal: bool) -> None:
        """Signals the child's whole group, falling back to the child alone."""
        if sys.platform == "win32":
            self._signal_process(proc, terminal)
            return
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL if terminal else signal.SIGTERM)
        except OSError:
            self._signal_process(proc, terminal)

    def _signal_process(self, proc: subprocess.Popen, terminal: bool) -> None:
        if terminal:
            proc.kill()
        else:
            proc.terminate()

    def _close_stdout(self) -> None:
        if self._proc is not None and self._proc.stdout is not None:
            try:
                self._proc.stdout.close()
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
        except (OSError, ValueError):
            pass  # expected: close() breaks a parked read by closing the pipe underneath it
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

    def _feed_output(self, chunk: bytes, decoder) -> None:
        text = decoder.decode(chunk)
        with self._output_lock:
            self._raw += chunk
            if text:
                self._decoded.append(text)
        self.normalizer.feed(chunk)  # outside the lock: parsing must not block read_output()

    def _flush_decoder(self, decoder) -> None:
        tail = decoder.decode(b"", final=True)  # a trailing partial sequence becomes U+FFFD
        if tail:
            with self._output_lock:
                self._decoded.append(tail)
