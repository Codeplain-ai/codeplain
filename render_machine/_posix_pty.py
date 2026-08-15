"""POSIX PTY backend for `TerminalProcess`.

One pseudoterminal backs the target's fds 0, 1 and 2. `spawn()` allocates it, launches
`pty_exec.py`, and completes a framed handshake that ends with an acknowledgment barrier:
the parent records the target's process group before the target is allowed to run, so
there is never a moment where a descendant exists that termination cannot reach.

A single reader thread owns `master_fd` for its whole lifetime. It is the only code that
reads from, writes to, or changes the terminal mode of that descriptor; every producer of
input enqueues a whole logical item and rings a doorbell instead of borrowing the fd.
"""

import codecs
import collections
import errno
import fcntl
import os
import select
import signal
import struct
import subprocess
import sys
import termios
import threading
import time
from typing import Callable, Deque, List, Optional, Sequence, Tuple

from plain2code_console import console
from plain2code_exceptions import RenderCancelledError
from render_machine import pty_exec
from render_machine.output_normalizer import OutputNormalizer
from render_machine.terminal_process import (
    DEFAULT_TERM,
    DRAIN_DEADLINE_SECONDS,
    DRAIN_MAX_BYTES,
    DRAIN_QUIET_PERIOD_SECONDS,
    GRACE_TICK_SECONDS,
    HANDSHAKE_TIMEOUT_SECONDS,
    INPUT_WRITE_BUDGET_BYTES,
    LAUNCHER_STDERR_CAP_BYTES,
    MAX_INPUT_ITEM_BYTES,
    MAX_PENDING_INPUT_BYTES,
    MAX_PENDING_INPUT_ITEMS,
    POLL_INTERVAL_SECONDS,
    READ_CHUNK_BYTES,
    REAP_DEADLINE_SECONDS,
    RESERVED_INPUT_BYTES,
    RESERVED_INPUT_ITEMS,
    SIGTERM_GRACE_PERIOD_SECONDS,
    TERMINAL_COLUMNS,
    TERMINAL_ROWS,
    InputDisposition,
    InputWriteResult,
    TerminalEnvironmentError,
    TerminalLaunchError,
    TerminalProcess,
    TerminalReaderError,
    child_environment,
)
from render_machine.terminal_queries import REASON_DISCARDED, REASON_WRITE_FAILED, TerminalQueryResponder

if sys.platform == "win32":  # pragma: no cover - the PTY backend is POSIX-only
    raise ImportError("render_machine._posix_pty is POSIX-only")

_LAUNCHER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pty_exec.py")

# Grace given to a launcher that never reached the target. It has not exec'd and never
# forks, so termination is immediate and the full grace would only slow failures down.
ROLLBACK_GRACE_SECONDS = 0.1

_OWNER_PARENT = "parent"
_OWNER_READER = "reader"

# Completion callback for one queued input item, resolved by whoever retires it.
ResolveCallback = Callable[[InputDisposition, Optional[BaseException]], None]


class _ProtocolError(Exception):
    """The launcher's status stream did not follow the handshake protocol."""


def _close_quietly(fd: Optional[int]) -> None:
    if fd is None:
        return
    try:
        os.close(fd)
    except OSError:
        pass


def _signal_group(pgid: int, sig: int) -> None:
    """The only killpg site in this module.

    ESRCH: the group is gone.
    EPERM: verified on macOS — killpg() returns EPERM, not ESRCH, when the group's only
    remaining member is our own unreaped zombie leader. That is the NORMAL state after a
    graceful exit, so it must not raise.
    """
    try:
        os.killpg(pgid, sig)
    except ProcessLookupError:  # ESRCH — nothing left
        return
    except PermissionError:  # EPERM — zombie-only group
        console.debug(f"killpg({pgid}, {sig}): EPERM, treating as terminal")


def _background_reap(proc: subprocess.Popen) -> None:
    try:
        proc.wait()
    except BaseException:  # nothing here can be reported anywhere useful
        pass


def _reap(proc: subprocess.Popen, deadline_seconds: float) -> None:
    """Bounded reap. SIGKILL is not instantaneous, so the foreground wait cannot be open-ended."""
    try:
        proc.wait(timeout=deadline_seconds)
    except subprocess.TimeoutExpired:
        console.debug(f"process {proc.pid} outlived the reap deadline; reaping it in the background")
        threading.Thread(target=_background_reap, args=(proc,), daemon=True).start()


class _Receipt:
    """Resolution of one queued input item. Resolved exactly once, by whoever retires it.

    `on_resolve` lets a producer observe that terminal transition without ever waiting for
    it, which is what the reader needs when it is the producer.
    """

    def __init__(self, on_resolve: Optional[ResolveCallback] = None) -> None:
        self._event = threading.Event()
        self.error: Optional[BaseException] = None
        self.disposition: Optional[InputDisposition] = None
        self.resolutions = 0
        self._on_resolve = on_resolve

    def resolve(self, disposition: InputDisposition, error: Optional[BaseException] = None) -> None:
        self.resolutions += 1
        if self._event.is_set():
            return
        self.disposition = disposition
        self.error = error
        self._event.set()
        if self._on_resolve is not None:
            try:
                self._on_resolve(disposition, error)
            except BaseException as exc:  # a completion callback must never strand the queue
                console.debug(f"input completion callback raised: {exc!r}")

    @property
    def resolved(self) -> bool:
        return self._event.is_set()


class _InputItem:
    """One whole logical write, plus the optional transaction that must bracket it."""

    def __init__(
        self,
        data: bytes,
        receipt: _Receipt,
        reserved: bool,
        prepare: Optional[Callable[[], None]],
        finish: Optional[Callable[[], None]],
        sequence: int,
    ) -> None:
        self.data = data
        self.receipt = receipt
        self.reserved = reserved
        self.prepare = prepare
        self.finish = finish
        self.sequence = sequence
        self.cursor = 0
        self.prepared = False

    def finish_once(self) -> Optional[BaseException]:
        """Closes the transaction the item opened, at most once, and never raises.

        Whoever retires the item runs it — the pump on completion, teardown on a close
        that takes the item mid-flight — so a prepared item can never be dropped with the
        terminal left in the mode `prepare` put it in.
        """
        if not self.prepared or self.finish is None:
            return None
        self.prepared = False
        try:
            self.finish()
        except BaseException as exc:
            return exc
        return None


class _InputQueue:
    """Bounded, byte-accounted, ordered input queue.

    Admission, the accepting flag, byte accounting, and sequence assignment share one
    lock. Dequeue is not completion: the item under the reader's cursor stays accounted
    for and keeps its receipt attached until its last native byte completes or teardown
    fails it, so capacity is released exactly once at that terminal transition.
    """

    def __init__(
        self,
        max_item_bytes: int = MAX_INPUT_ITEM_BYTES,
        max_pending_bytes: int = MAX_PENDING_INPUT_BYTES,
        reserved_bytes: int = RESERVED_INPUT_BYTES,
        max_pending_items: int = MAX_PENDING_INPUT_ITEMS,
        reserved_items: int = RESERVED_INPUT_ITEMS,
    ) -> None:
        self._lock = threading.Lock()
        self._items: Deque[_InputItem] = collections.deque()
        self._current: Optional[_InputItem] = None
        self._pending_bytes = 0
        self._sequence = 0
        self._accepting = True
        self._max_item_bytes = max_item_bytes
        self._max_pending_bytes = max_pending_bytes
        self._reserved_bytes = reserved_bytes
        self._max_pending_items = max_pending_items
        self._reserved_items = reserved_items

    def submit(
        self,
        data: bytes,
        reserved: bool = False,
        prepare: Optional[Callable[[], None]] = None,
        finish: Optional[Callable[[], None]] = None,
        on_resolve: Optional[ResolveCallback] = None,
    ) -> Tuple[InputWriteResult, _Receipt]:
        receipt = _Receipt(on_resolve)
        size = len(data)  # measured on the caller's view; nothing is copied until it is admitted
        enqueued = False
        with self._lock:
            byte_budget, item_budget = self._budget(reserved)
            queued = len(self._items) + (0 if self._current is None else 1)
            if not self._accepting:
                result = InputWriteResult(InputDisposition.CLOSED, 0)
            elif size == 0:
                # Nothing to deliver, so it never becomes an entry: an empty item would
                # otherwise grow the queue without ever touching the byte budget.
                result = InputWriteResult(InputDisposition.ACCEPTED, 0)
            elif size > self._max_item_bytes:
                result = InputWriteResult(InputDisposition.BACKPRESSURE, 0)
            elif self._pending_bytes + size > byte_budget or queued >= item_budget:
                result = InputWriteResult(InputDisposition.BACKPRESSURE, 0)
            else:
                self._sequence += 1
                self._items.append(_InputItem(bytes(data), receipt, reserved, prepare, finish, self._sequence))
                self._pending_bytes += size
                result = InputWriteResult(InputDisposition.ACCEPTED, size)
                enqueued = True
        if not enqueued:  # nothing will retire it later, so it resolves here
            receipt.resolve(result.disposition)
        return result, receipt

    def _budget(self, reserved: bool) -> Tuple[int, int]:
        """Remaining admission budget in both dimensions, bytes first."""
        if reserved:
            return self._max_pending_bytes, self._max_pending_items
        return self._max_pending_bytes - self._reserved_bytes, self._max_pending_items - self._reserved_items

    def has_pending(self) -> bool:
        with self._lock:
            return self._current is not None or bool(self._items)

    def pending_bytes(self) -> int:
        with self._lock:
            return self._pending_bytes

    def pending_items(self) -> int:
        with self._lock:
            return len(self._items) + (0 if self._current is None else 1)

    def current(self) -> Optional[_InputItem]:
        """The item under the cursor, promoting the next waiting item when there is none."""
        with self._lock:
            if self._current is None and self._items:
                self._current = self._items.popleft()
            return self._current

    def complete_current(self, error: Optional[BaseException] = None) -> None:
        with self._lock:
            item = self._current
            if item is None:
                return
            self._current = None
            self._pending_bytes -= len(item.data)
        item.receipt.resolve(InputDisposition.CLOSED if error is not None else InputDisposition.ACCEPTED, error)

    def stop_accepting(self, closing: threading.Event) -> None:
        """Marks the queue non-accepting and signals shutdown under the same lock.

        No producer can then enqueue behind the reader's fail_all().
        """
        with self._lock:
            self._accepting = False
            closing.set()

    def close_and_fail_all(self, error: Optional[BaseException] = None) -> List[_InputItem]:
        with self._lock:
            self._accepting = False
            items = list(self._items)
            self._items.clear()
            if self._current is not None:
                items.append(self._current)
                self._current = None
            self._pending_bytes = 0
        for item in items:  # callbacks run outside the lock and cannot re-enter the queue
            # The in-flight item may hold an open transaction; closing it is teardown's
            # job now, and it happens before the receipt reports the item retired.
            finish_error = item.finish_once()
            try:
                item.receipt.resolve(InputDisposition.CLOSED, error or finish_error)
            except BaseException as exc:  # a receipt must never strand its siblings
                console.debug(f"input receipt callback raised: {exc!r}")
        return items


class _ReaderBundle:
    """The descriptors whose ownership moves from the parent to the reader in one step.

    `owner` is the single field that decides. Rollback and reader consult it, so they can
    never disagree and there is no state in which a descriptor has left one owner without
    reaching the other.
    """

    def __init__(self, master_fd: int, wakeup_r: int, err_w: int) -> None:
        self.owner = _OWNER_PARENT
        self.master_fd: Optional[int] = master_fd
        self.wakeup_r: Optional[int] = wakeup_r
        self.err_w: Optional[int] = err_w
        self._lock = threading.Lock()

    def _take(self, name: str) -> Optional[int]:
        with self._lock:  # swap first, close only what the swap returned
            fd = getattr(self, name)
            setattr(self, name, None)
            return fd

    def take_master(self) -> Optional[int]:
        return self._take("master_fd")

    def take_wakeup_r(self) -> Optional[int]:
        return self._take("wakeup_r")

    def take_err_w(self) -> Optional[int]:
        return self._take("err_w")

    def close_all(self) -> None:
        for name in ("master_fd", "wakeup_r", "err_w"):
            _close_quietly(self._take(name))


class _CappedDiagnostic:
    """Keeps the head and the tail of a stream while the middle keeps being discarded."""

    def __init__(self, cap: int = LAUNCHER_STDERR_CAP_BYTES) -> None:
        self._cap = cap
        self._head = bytearray()
        self._tail = bytearray()
        self.total = 0

    def feed(self, chunk: bytes) -> None:
        self.total += len(chunk)
        if len(self._head) < self._cap:
            room = self._cap - len(self._head)
            self._head += chunk[:room]
            chunk = chunk[room:]
        if chunk:
            self._tail += chunk
            del self._tail[: max(0, len(self._tail) - self._cap)]

    def text(self) -> str:
        head = bytes(self._head).decode("utf-8", "replace")
        if not self._tail:
            return head
        omitted = self.total - len(self._head) - len(self._tail)
        return f"{head}\n...[{omitted} bytes omitted]...\n" + bytes(self._tail).decode("utf-8", "replace")


class _HandshakeParser:
    """Strict bounded state machine over the launcher's framed status records.

    Accepts exactly STARTED -> SESSION_READY -> EOF as success. Everything else — unknown
    kinds, duplicate or out-of-order markers, a marker carrying a payload, a declared
    length above the cap, a truncated record at EOF, trailing bytes after FAILED — is a
    protocol failure on the environment-error channel.
    """

    def __init__(self) -> None:
        self._buffer = bytearray()
        self.started = False
        self.session_ready = False
        self.failure_payload: Optional[bytes] = None

    def feed(self, chunk: bytes) -> None:
        self._buffer += chunk
        while True:
            if self.failure_payload is not None:
                if self._buffer:
                    raise _ProtocolError("the launcher wrote trailing bytes after its failure record")
                return
            if len(self._buffer) < pty_exec.HEADER_SIZE:
                return
            kind = self._buffer[0]
            length = int.from_bytes(bytes(self._buffer[1:5]), "big")
            self._validate_header(kind, length)
            if len(self._buffer) < pty_exec.HEADER_SIZE + length:
                return
            payload = bytes(self._buffer[pty_exec.HEADER_SIZE : pty_exec.HEADER_SIZE + length])
            del self._buffer[: pty_exec.HEADER_SIZE + length]
            self._accept(kind, payload)

    def _validate_header(self, kind: int, length: int) -> None:
        if kind not in (pty_exec.STARTED, pty_exec.SESSION_READY, pty_exec.FAILED):
            raise _ProtocolError(f"unknown handshake record type 0x{kind:02x}")
        if length > pty_exec.MAX_PAYLOAD:  # rejected before allocating or waiting for a body
            raise _ProtocolError(f"handshake record declares {length} bytes, above the {pty_exec.MAX_PAYLOAD} cap")
        if kind != pty_exec.FAILED and length:
            raise _ProtocolError("a handshake marker record must carry no payload")

    def _accept(self, kind: int, payload: bytes) -> None:
        if kind == pty_exec.STARTED:
            if self.started:
                raise _ProtocolError("duplicate STARTED record")
            self.started = True
        elif kind == pty_exec.SESSION_READY:
            if not self.started or self.session_ready:
                raise _ProtocolError("out-of-order SESSION_READY record")
            self.session_ready = True
        else:
            if not self.started:
                raise _ProtocolError("FAILED record before STARTED")
            self.failure_payload = payload

    def eof(self) -> None:
        if self._buffer:
            raise _ProtocolError("the launcher's status stream ended mid-record")
        if not self.started:
            raise _ProtocolError("the interpreter died before running the launcher")
        if not self.session_ready:
            raise _ProtocolError("the launcher exited after STARTED without a ready session")


class PosixPtyProcess(TerminalProcess):
    """One command, one pseudoterminal, one reader thread."""

    def __init__(self) -> None:
        self.reader_failed = threading.Event()
        self.reader_exc: Optional[BaseException] = None

        self._proc: Optional[subprocess.Popen] = None
        self._pgid: Optional[int] = None
        self._reaped = False
        self._spawned = False
        self._closed = False
        self._acked = False
        self._input_driver: Optional[object] = None
        self._stop_event = threading.Event()

        self._bundle: Optional[_ReaderBundle] = None
        self._reader: Optional[threading.Thread] = None
        self._gate = threading.Event()
        self._closing = threading.Event()
        self._input_queue = _InputQueue()
        self._drain_deadline: Optional[float] = None
        self._veof_byte = b"\x04"
        self._veof_saved: Optional[list] = None

        self._fd_lock = threading.Lock()
        self._pending_master_fd: Optional[int] = None
        self._pending_slave_fd: Optional[int] = None
        self._child_fds: Tuple[int, ...] = ()
        self._wakeup_w: Optional[int] = None
        self._err_r: Optional[int] = None
        self._status_r: Optional[int] = None
        self._ack_w: Optional[int] = None

        self._output_lock = threading.Lock()
        self._decoded: List[str] = []
        self._raw = bytearray()
        self.launcher_stderr = _CappedDiagnostic()

        # The parser runs live in the reader through this byte-feed hook, because terminals
        # answer queries: a render-afterwards parser would leave a querying target hanging.
        self.query_responder = TerminalQueryResponder(self._admit_reply)
        self.normalizer = OutputNormalizer(reply_handler=self.query_responder.answer)
        self._byte_sink: Callable[[bytes], None] = self.normalizer.feed

    # ---------------------------------------------------------------- public API

    def spawn(
        self,
        command: Sequence[str],
        cwd: Optional[str] = None,
        env: Optional[dict] = None,
        terminal_size: Tuple[int, int] = (TERMINAL_COLUMNS, TERMINAL_ROWS),
        stop_event: Optional[threading.Event] = None,
        input_driver: Optional[object] = None,
        handshake_timeout: float = HANDSHAKE_TIMEOUT_SECONDS,
        pre_ack_delay: float = 0.0,
    ) -> None:
        """Allocates the terminal, launches the target, and returns once it is running.

        `pre_ack_delay` holds the parent's acknowledgment for a bounded time. It exists so
        the barrier's window can be driven deterministically from tests; production
        callers leave it at zero.
        """
        if self._spawned:
            raise RuntimeError("PosixPtyProcess instances are single-use")
        self._spawned = True
        self._stop_event = stop_event if stop_event is not None else threading.Event()
        self._input_driver = input_driver
        deadline = time.monotonic() + handshake_timeout
        try:
            self._check_cancelled()
            self._open_terminal(terminal_size)
            self._open_channels()
            self._start_child(command, cwd, env)
            self._hand_over_to_reader()
            self._run_handshake(deadline, pre_ack_delay)
            self._close_owned("_status_r")  # the handshake has resolved
        except BaseException:
            self._rollback()
            raise

    def poll(self) -> Optional[int]:
        if self._proc is None:
            return None
        returncode = self._proc.poll()
        if returncode is not None:
            # Popen.poll() reaps, so the pgid may now be recycled; no group signal is
            # ever sent again.
            self._reaped = True
            # The execution outcome is observed, so no client is left to answer.
            self.query_responder.quiesce()
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

    def write_input(self, data: bytes) -> InputWriteResult:
        result, _ = self._input_queue.submit(data)
        if result.disposition is InputDisposition.ACCEPTED:
            self._ring_doorbell()
        return result

    def normalized_output(self) -> str:
        """The rendered transcript so far. Cumulative, unlike `read_output()`."""
        return self.normalizer.text()

    @property
    def terminal_reply_failed(self) -> bool:
        return self.query_responder.reply_failed

    def terminal_reply_detail(self) -> str:
        return self.query_responder.failure_detail()

    def terminate_tree(self, grace: float = SIGTERM_GRACE_PERIOD_SECONDS) -> None:
        """Signals the recorded group, escalates on the clock, and reaps last.

        A reader failure observed here is recorded by the reader and deliberately not
        acted on: returning early would skip the SIGKILL escalation the sequence exists
        for. The caller inspects `reader_failed` afterwards.
        """
        self.query_responder.quiesce()
        proc = self._proc
        if proc is None or self._reaped:
            return
        pgid = self._pgid
        try:
            try:
                self._deliver(proc, pgid, signal.SIGTERM)
                self._deliver(proc, pgid, signal.SIGCONT)
                deadline = time.monotonic() + grace  # independent clock — NOT stop_event
                while time.monotonic() < deadline:  # never waits on the leader either
                    self._grace_tick()
            finally:
                # Unconditional: an interruption mid-grace must still escalate.
                self._deliver(proc, pgid, signal.SIGKILL)
        finally:
            _reap(proc, REAP_DEADLINE_SECONDS)
            self._reaped = True

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.query_responder.quiesce()  # before either input pump stops
        self._drain_deadline = time.monotonic() + DRAIN_DEADLINE_SECONDS
        self._input_queue.stop_accepting(self._closing)
        self._ring_doorbell()
        if self._reader is not None and self._reader.ident is not None:  # None when it never started
            self._reader.join(timeout=DRAIN_DEADLINE_SECONDS + REAP_DEADLINE_SECONDS)
        self._close_owned("_wakeup_w")
        self._close_owned("_err_r")
        self._close_owned("_status_r")
        self._close_owned("_ack_w")
        if self._proc is not None and self._proc.stderr is not None:
            self._proc.stderr.close()
        if self._bundle is not None and self._bundle.owner == _OWNER_PARENT:
            self._bundle.close_all()  # no reader ever took them

    # ------------------------------------------------------------- spawn helpers

    def _open_terminal(self, terminal_size: Tuple[int, int]) -> None:
        try:
            master_fd, slave_fd = os.openpty()
        except OSError as exc:
            raise TerminalEnvironmentError(f"Could not allocate a pseudoterminal: {exc}") from exc
        try:
            columns, rows = terminal_size
            self.normalizer.resize(columns, rows)
            fcntl.ioctl(slave_fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, columns, 0, 0))
            self._configure_slave(slave_fd)
            os.set_blocking(master_fd, False)
        except BaseException:
            _close_quietly(master_fd)
            _close_quietly(slave_fd)
            raise
        self._pending_master_fd = master_fd
        self._pending_slave_fd = slave_fd

    def _configure_slave(self, slave_fd: int) -> None:
        """Sane termios with echo on. ONLCR is left at its default: Option A means real
        terminal semantics, and the \\r\\n is dealt with in normalization."""
        attrs = termios.tcgetattr(slave_fd)
        attrs[0] |= termios.ICRNL
        attrs[1] |= termios.OPOST | termios.ONLCR
        attrs[3] |= termios.ICANON | termios.ISIG | termios.ECHO | termios.IEXTEN
        termios.tcsetattr(slave_fd, termios.TCSANOW, attrs)
        self._veof_byte = bytes([attrs[6][termios.VEOF][0]])

    def _open_channels(self) -> None:
        """Pre-registers every parent-side owner before the API that fills it.

        Nothing is published until every descriptor and both objects exist, so a failure
        part-way through closes exactly what it opened and reports on the environment
        channel rather than leaking ownerless descriptors behind a raw OSError.
        """
        opened: List[int] = []

        def pipe() -> Tuple[int, int]:
            read_fd, write_fd = os.pipe()
            opened.extend((read_fd, write_fd))
            return read_fd, write_fd

        try:
            status_r, status_w = pipe()
            ack_r, ack_w = pipe()
            wakeup_r, wakeup_w = pipe()
            err_r, err_w = pipe()
            os.set_blocking(wakeup_r, False)
            os.set_blocking(wakeup_w, False)
            master_fd = self._pending_master_fd
            assert master_fd is not None
            bundle = _ReaderBundle(master_fd, wakeup_r, err_w)
            reader = threading.Thread(target=self._reader_main, name="codeplain-pty-reader", daemon=True)
        except BaseException as exc:
            for fd in opened:
                _close_quietly(fd)
            if isinstance(exc, (OSError, RuntimeError)):  # the terminal's own resources ran out
                raise TerminalEnvironmentError(f"Could not open the terminal's control channels: {exc}") from exc
            raise

        self._status_r = status_r
        self._ack_w = ack_w
        self._wakeup_w = wakeup_w
        self._err_r = err_r
        self._pending_master_fd = None  # the bundle owns it from here
        self._bundle = bundle
        self._reader = reader
        self._child_fds = (status_w, ack_r)

    def _start_child(self, command: Sequence[str], cwd: Optional[str], env: Optional[dict]) -> None:
        status_w, ack_r = self._child_fds
        slave_fd = self._pending_slave_fd
        assert slave_fd is not None
        argv = [
            sys.executable,
            "-I",  # isolated: no PYTHONPATH, no user site
            "-S",  # no site processing, so no sitecustomize and no .pth can fork before STARTED
            _LAUNCHER,
            str(slave_fd),
            str(status_w),
            str(ack_r),
            "--",
            *command,
        ]
        try:
            self._proc = subprocess.Popen(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                pass_fds=(slave_fd, status_w, ack_r),
                close_fds=True,
                cwd=cwd,
                env=self._child_env(env),
            )
        except OSError as exc:
            raise TerminalEnvironmentError(f"Could not start the terminal launcher: {exc}") from exc
        finally:
            # Correctness for the first two: holding them means the master never reaches
            # EOF and the launcher's exec is never observable.
            for fd in (slave_fd, status_w, ack_r):
                _close_quietly(fd)
            self._child_fds = ()
            self._pending_slave_fd = None

    def _child_env(self, env: Optional[dict]) -> dict:
        child_env = child_environment(env)
        term = child_env.get("TERM")
        child_env["TERM"] = term if term else DEFAULT_TERM
        # git reads /dev/tty directly, so neither the VEOF nor a redirected stdin can
        # reach a credential prompt; failing is the only bounded outcome.
        child_env["GIT_TERMINAL_PROMPT"] = "0"
        return child_env

    def _hand_over_to_reader(self) -> None:
        """Starts the gated reader and commits ownership in a single field assignment."""
        assert self._bundle is not None and self._reader is not None
        try:
            self._reader.start()
            self._bundle.owner = _OWNER_READER
        finally:
            self._gate.set()  # an unreleased gate is unrecoverable, so this is never conditional
        self._check_reader_failed()

    # ---------------------------------------------------------------- handshake

    def _run_handshake(self, deadline: float, pre_ack_delay: float) -> None:
        parser = _HandshakeParser()
        assert self._proc is not None and self._proc.stderr is not None
        status_r, err_r = self._status_r, self._err_r
        assert status_r is not None and err_r is not None
        stderr_fd = self._proc.stderr.fileno()
        watched = {status_r, err_r, stderr_fd}
        while True:
            self._check_cancelled()
            self._check_reader_failed()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TerminalLaunchError(self._launch_message("the launcher hung before exec"))
            readable, _, _ = select.select(sorted(watched), [], [], min(remaining, POLL_INTERVAL_SECONDS))
            if stderr_fd in readable and not self._drain_launcher_stderr(stderr_fd):
                watched.discard(stderr_fd)
            if err_r in readable:
                self._consume_reader_edge(watched, err_r)
            if status_r in readable and self._advance_handshake(parser, status_r, deadline, pre_ack_delay):
                return

    def _advance_handshake(
        self, parser: _HandshakeParser, status_r: int, deadline: float, pre_ack_delay: float
    ) -> bool:
        """Feeds one status chunk. Returns True once exec has been observed."""
        chunk = os.read(status_r, READ_CHUNK_BYTES)
        try:
            if not chunk:
                parser.eof()
                return True
            parser.feed(chunk)
        except _ProtocolError as exc:
            raise TerminalLaunchError(self._launch_message(str(exc))) from exc
        if parser.failure_payload is not None:
            reason = parser.failure_payload.decode("utf-8", "replace")
            raise TerminalLaunchError(self._launch_message(f"the launcher failed: {reason}"))
        if parser.session_ready and not self._acked:
            self._acknowledge(deadline, pre_ack_delay)
        return False

    def _acknowledge(self, deadline: float, pre_ack_delay: float) -> None:
        """Records the group, delivers the no-driver VEOF, and only then releases the target."""
        assert self._proc is not None
        self._pgid = self._proc.pid  # recorded BEFORE the target can run
        if self._input_driver is None:
            self._inject_veof(deadline)
        if pre_ack_delay > 0:
            self._wait_pre_ack(pre_ack_delay, deadline)
        self._acked = True
        ack_w = self._ack_w
        assert ack_w is not None
        try:
            os.write(ack_w, b"\x01")
        except BrokenPipeError:
            # The launcher gave up first and its own reason is already on the status
            # pipe; recovery continues under the same deadline, never a fresh budget.
            console.debug("the launcher closed the acknowledgment pipe before the parent acknowledged")
        self._close_owned("_ack_w")

    def _wait_pre_ack(self, delay: float, deadline: float) -> None:
        until = min(time.monotonic() + delay, deadline)
        while time.monotonic() < until:
            self._check_cancelled()
            self._check_reader_failed()
            time.sleep(min(POLL_INTERVAL_SECONDS, max(0.0, until - time.monotonic())))

    def _inject_veof(self, deadline: float) -> None:
        result, receipt = self._input_queue.submit(
            self._veof_byte, reserved=True, prepare=self._veof_prepare, finish=self._veof_restore
        )
        if result.disposition is not InputDisposition.ACCEPTED:
            raise TerminalEnvironmentError(f"The spawn-time EOF was not admitted: {result.disposition.value}")
        self._ring_doorbell()
        self._await_receipt(receipt, deadline)

    def _await_receipt(self, receipt: _Receipt, deadline: float) -> None:
        while not receipt.resolved:
            self._check_cancelled()
            self._check_reader_failed()
            if time.monotonic() >= deadline:
                raise TerminalEnvironmentError("The spawn-time EOF was not delivered before the handshake deadline")
            time.sleep(POLL_INTERVAL_SECONDS / 4)
        if receipt.error is not None:
            raise TerminalEnvironmentError(f"The spawn-time EOF failed: {receipt.error!r}") from receipt.error
        if receipt.disposition is not InputDisposition.ACCEPTED:
            # Teardown resolves receipts before it publishes the reader's failure, so a
            # discarded item usually means the reader died; let it publish, then classify.
            if self._reader is not None and self._reader.ident is not None:
                self._reader.join(timeout=POLL_INTERVAL_SECONDS * 4)
            self._check_reader_failed()
            raise TerminalEnvironmentError("The spawn-time EOF was discarded before delivery")

    def _drain_launcher_stderr(self, stderr_fd: int) -> bool:
        """Keeps the launcher from blocking on a full stderr pipe. False once it is at EOF."""
        chunk = os.read(stderr_fd, READ_CHUNK_BYTES)
        if not chunk:
            return False
        self.launcher_stderr.feed(chunk)  # reads continue after the cap; only retention stops
        return True

    def _consume_reader_edge(self, watched: set, err_r: int) -> None:
        """A readable err_r means 'consult reader_failed', not 'the reader failed'."""
        try:
            os.read(err_r, READ_CHUNK_BYTES)
        except OSError:
            pass
        self._check_reader_failed()
        watched.discard(err_r)  # EOF is level-triggered and permanent
        self._close_owned("_err_r")  # ownership transfer, so it needs the swap

    def _launch_message(self, reason: str) -> str:
        diagnostic = self.launcher_stderr.text()
        if diagnostic:
            return f"{reason}. Launcher output:\n{diagnostic}"
        return f"{reason}."

    def _check_cancelled(self) -> None:
        if self._stop_event.is_set():
            raise RenderCancelledError()

    def _check_reader_failed(self) -> None:
        if self.reader_failed.is_set():
            raise TerminalReaderError(f"The terminal output reader failed: {self.reader_exc!r}")

    # ------------------------------------------------------------------- reader

    def _reader_main(self) -> None:
        self._gate.wait()
        assert self._bundle is not None
        if self._bundle.owner != _OWNER_READER:
            return  # the parent still owns everything; touch nothing, publish nothing
        reader_exc: Optional[BaseException] = None
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        try:
            self._reader_loop(decoder)
        except BaseException as exc:  # nothing reaches threading.excepthook
            reader_exc = exc
        finally:
            try:
                self._input_queue.close_and_fail_all()
                for fd in (self._bundle.take_master(), self._bundle.take_wakeup_r()):
                    _close_quietly(fd)  # independent: one failing close cannot skip the rest
                self._flush_decoder(decoder)
                self.normalizer.finalize()  # the same end-of-stream flush, on the rendered channel
            except BaseException as exc:  # finalization can fail too
                reader_exc = reader_exc or exc
            finally:
                self.reader_exc = reader_exc  # stored while still unobservable
                if reader_exc is not None:
                    self.reader_failed.set()
                # LAST — the single edge that publishes "the reader is done and owns nothing"
                _close_quietly(self._bundle.take_err_w())

    def _reader_loop(self, decoder) -> None:
        assert self._bundle is not None
        master_fd = self._bundle.master_fd
        wakeup_r = self._bundle.wakeup_r
        assert master_fd is not None and wakeup_r is not None
        while True:
            want_write = [master_fd] if self._input_queue.has_pending() else []
            readable, writable = self._select([master_fd, wakeup_r], want_write, POLL_INTERVAL_SECONDS)
            if wakeup_r in readable:
                _drain_doorbell(wakeup_r)  # bytes coalesce; state carries the meaning
            if self._closing.is_set():
                self._input_queue.close_and_fail_all()
                self._drain_remaining(master_fd, decoder)
                return
            if master_fd in readable and not self._read_once(master_fd, decoder):
                return  # output always wins over queued input
            if master_fd in writable or self._input_queue.has_pending():
                self._flush_input(master_fd, INPUT_WRITE_BUDGET_BYTES)

    def _select(self, rlist, wlist, timeout):
        readable, writable, _ = select.select(rlist, wlist, [], timeout)
        return readable, writable

    def _read_master(self, fd: int, size: int) -> bytes:
        return os.read(fd, size)

    def _write_master(self, fd: int, data: bytes) -> int:
        return os.write(fd, data)

    def _read_once(self, master_fd: int, decoder) -> bool:
        try:
            chunk = self._read_master(master_fd, READ_CHUNK_BYTES)
        except BlockingIOError:
            return True
        except OSError as exc:
            if exc.errno == errno.EIO:  # normal PTY EOF on Linux once the last slave closes
                return False
            raise
        if not chunk:  # normal EOF elsewhere
            return False
        self._feed_output(chunk, decoder)
        return True

    def _feed_output(self, chunk: bytes, decoder) -> None:
        text = decoder.decode(chunk)
        with self._output_lock:
            self._raw += chunk
            if text:
                self._decoded.append(text)
        self._byte_sink(chunk)  # outside the output lock: parsing must not block read_output()

    def _flush_decoder(self, decoder) -> None:
        tail = decoder.decode(b"", final=True)  # a trailing partial sequence becomes U+FFFD
        if tail:
            with self._output_lock:
                self._decoded.append(tail)

    def _flush_input(self, master_fd: int, budget: int) -> None:
        """Services the FIFO through one retained cursor, bounded so input cannot starve output."""
        written = 0
        while written < budget:
            item = self._input_queue.current()
            if item is None:
                return
            try:
                if item.prepare is not None and not item.prepared:
                    item.prepare()
                    item.prepared = True
                while item.cursor < len(item.data):
                    try:
                        count = self._write_master(master_fd, item.data[item.cursor :])
                    except BlockingIOError:
                        return  # EAGAIN retains the tail and returns to select()
                    item.cursor += count
                    written += count
                    if written >= budget and item.cursor < len(item.data):
                        return  # a short write retains the suffix for the next iteration
            except BaseException as exc:
                self._complete_item(item, exc)
                raise
            error = self._complete_item(item, None)
            if error is not None:
                raise error

    def _complete_item(self, item: _InputItem, error: Optional[BaseException]) -> Optional[BaseException]:
        finish_error = item.finish_once()  # the restore is part of the item's contract
        error = error or finish_error
        self._input_queue.complete_current(error)
        return error

    def _drain_remaining(self, master_fd: int, decoder) -> None:
        """Catches output already in flight. Bounded by time, by bytes, and by a quiet period."""
        deadline = self._drain_deadline or (time.monotonic() + DRAIN_DEADLINE_SECONDS)
        drained = 0
        while drained < DRAIN_MAX_BYTES:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            readable, _ = self._select([master_fd], [], min(remaining, DRAIN_QUIET_PERIOD_SECONDS))
            if not readable:
                return  # nothing more is in flight
            try:
                chunk = self._read_master(master_fd, READ_CHUNK_BYTES)
            except (BlockingIOError, OSError):
                return
            if not chunk:
                return
            # The same feed path as the loop: drained output belongs on the decoded
            # channel too. A query seen here is render-only — replies are already quiesced.
            self._feed_output(chunk, decoder)
            drained += len(chunk)

    # ------------------------------------------------------------- query replies

    def _admit_reply(self, payload: bytes, on_complete: Callable[[Optional[str]], None]) -> None:
        """One non-blocking whole-item admission of a terminal reply, from the reader.

        Replies take the reserved partition because they are terminal protocol: a caller
        saturating the queue with input must not be able to starve a required response.
        They are never counted as caller input and never affect the input-driver
        diagnostic. The queue's cursor preserves the reply across short writes.
        """
        result, _ = self._input_queue.submit(payload, reserved=True, on_resolve=_reply_resolution(on_complete))
        if result.disposition is InputDisposition.ACCEPTED:
            self._ring_doorbell()

    # ----------------------------------------------------------- VEOF injection

    def _veof_prepare(self) -> None:
        """Snapshots the terminal mode and clears echo, executed by the reader alone."""
        assert self._bundle is not None and self._bundle.master_fd is not None
        fd = self._bundle.master_fd
        self._veof_saved = termios.tcgetattr(fd)
        attrs = termios.tcgetattr(fd)
        attrs[3] &= ~(termios.ECHO | getattr(termios, "ECHOCTL", 0))
        termios.tcsetattr(fd, termios.TCSANOW, attrs)  # TCSAFLUSH could discard the byte

    def _veof_restore(self) -> None:
        saved, self._veof_saved = self._veof_saved, None
        if saved is None:
            return
        assert self._bundle is not None and self._bundle.master_fd is not None
        termios.tcsetattr(self._bundle.master_fd, termios.TCSANOW, saved)

    # ------------------------------------------------------------ teardown bits

    def _deliver(self, proc: subprocess.Popen, pgid: Optional[int], sig: int) -> None:
        if pgid is not None:
            _signal_group(pgid, sig)
            return
        try:  # pre-ack: the launcher has not exec'd and never forks, so the PID suffices
            proc.send_signal(sig)
        except (ProcessLookupError, PermissionError, ValueError):
            pass

    def _grace_tick(self) -> None:
        time.sleep(GRACE_TICK_SECONDS)

    def _rollback(self) -> None:
        try:
            if self._proc is not None:
                self.terminate_tree(ROLLBACK_GRACE_SECONDS)
        finally:
            try:
                self.close()
            finally:  # nothing reached an owner yet on the earliest failure paths
                _close_quietly(self._take_owned("_pending_master_fd"))
                _close_quietly(self._take_owned("_pending_slave_fd"))

    def _ring_doorbell(self) -> None:
        """A notification, not a message: producers mutate state first, then ring."""
        with self._fd_lock:  # held so close() cannot free the number under the write
            fd = self._wakeup_w
            if fd is None:
                return
            try:
                os.write(fd, b"\x01")
            except OSError:
                # EAGAIN means the pipe is already readable, EPIPE/EBADF mean the reader
                # is gone — which is the outcome the write was asking for.
                pass

    def _take_owned(self, name: str) -> Optional[int]:
        with self._fd_lock:
            fd = getattr(self, name)
            setattr(self, name, None)
            return fd

    def _close_owned(self, name: str) -> None:
        _close_quietly(self._take_owned(name))


def _reply_resolution(on_complete: Callable[[Optional[str]], None]) -> ResolveCallback:
    """Maps one queue resolution onto the responder's delivered / not-delivered contract."""

    def resolved(disposition: InputDisposition, error: Optional[BaseException]) -> None:
        if error is not None:
            on_complete(f"{REASON_WRITE_FAILED}: {error!r}")
        elif disposition is InputDisposition.ACCEPTED:
            on_complete(None)
        else:
            on_complete(f"{REASON_DISCARDED} ({disposition.value})")

    return resolved


def _drain_doorbell(fd: int) -> None:
    while True:
        try:
            if not os.read(fd, READ_CHUNK_BYTES):
                return
        except OSError:
            return
