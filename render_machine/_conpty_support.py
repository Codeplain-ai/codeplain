"""Platform-neutral parts of the Windows ConPTY backend.

`_conpty.py` binds kernel32 at import time and can only be imported on Windows, so the
rules that need no Windows API live here instead: the marshaling `CreateProcessW` requires,
the bounded input queue, and the writer protocol that owns every write to the
pseudoconsole's input pipe. Splitting them out is what lets them run in the test suite on
every platform rather than only on a Windows runner.

The input writer is a thread because the pseudoconsole's input pipe is an anonymous pipe,
and anonymous pipes are synchronous: a target that stops reading its input leaves
`WriteFile` blocked until somebody cancels it. Only this thread's handle is registered for
cancellation, so every producer — caller input, terminal-query replies, the graceful
control byte — enqueues a whole item here instead of writing to the pipe itself.
"""

import collections
import subprocess
import threading
import time
from enum import Enum
from typing import Callable, Deque, List, Mapping, Optional, Sequence, Tuple

from plain2code_console import console
from render_machine.terminal_process import (
    MAX_INPUT_ITEM_BYTES,
    MAX_PENDING_INPUT_BYTES,
    MAX_PENDING_INPUT_ITEMS,
    RESERVED_INPUT_BYTES,
    RESERVED_INPUT_ITEMS,
    InputDisposition,
    InputWriteResult,
    TerminalEnvironmentError,
)
from render_machine.terminal_queries import REASON_DISCARDED, REASON_WRITE_FAILED

NUL = "\x00"

# How often the foreground retries `CancelSynchronousIo()` while waiting for a control item
# or for the writer to join. A cancel issued before the writer has entered its write reports
# ERROR_NOT_FOUND and does nothing, so the call is a tick rather than a one-shot.
CANCEL_TICK_SECONDS = 0.02

# How long an idle writer parks on the queue before looking at its stopping flag again.
WRITER_IDLE_TICK_SECONDS = 0.05

# How long teardown waits for the writer to leave a synchronous write before the whole
# session is handed to the finalizer.
WRITER_JOIN_DEADLINE_SECONDS = 3.0

# A target that queries in a loop against a closed channel loses one item per query, so the
# loss is logged as a sample plus a count rather than once per item.
DROP_LOG_INTERVAL_SECONDS = 5.0

# Resolution of one queued item, as the producer sees it.
ResolveCallback = Callable[[InputDisposition, Optional[BaseException]], None]


# --------------------------------------------------------------------- marshaling


def _reject_nul(value: str, description: str) -> None:
    """`CreateProcessW` takes NUL-terminated strings, so an embedded NUL truncates silently.

    `subprocess` performs this check for its callers; calling the API through ctypes bypasses
    it, so it is re-established here rather than assumed.
    """
    if NUL in value:
        raise TerminalEnvironmentError(f"{description} contains a NUL character, which Windows cannot carry.")


def build_command_line(command: Sequence[str]) -> str:
    """One command line quoted to the MS C runtime rules.

    `subprocess.list2cmdline()` rather than a second dialect, so a command spawned through
    the ConPTY backend produces the same argv as the same command spawned through `Popen`.
    """
    argv = list(command)
    if not argv:
        raise TerminalEnvironmentError("The command to run is empty.")
    for index, argument in enumerate(argv):
        _reject_nul(argument, f"Argument {index} of the command")
    return subprocess.list2cmdline(argv)


def validate_working_directory(cwd: Optional[str]) -> Optional[str]:
    if cwd is not None:
        _reject_nul(cwd, "The working directory")
    return cwd


def build_environment_block(env: Mapping[str, str]) -> str:
    """`KEY=VALUE` entries, each NUL-terminated, sorted case-insensitively.

    The sort order is documented as a requirement of the environment block, not a
    convention. The caller copies the result into a unicode buffer, whose own terminator
    supplies the second NUL the block ends with.
    """
    entries = []
    for name, value in sorted(env.items(), key=lambda item: item[0].upper()):
        if not name:
            raise TerminalEnvironmentError("An environment variable name is empty.")
        if "=" in name:
            # The block's own name/value separator: a name carrying one silently reshapes
            # the block into different variables.
            raise TerminalEnvironmentError(f"Environment variable name {name!r} contains '='.")
        _reject_nul(name, f"Environment variable name {name!r}")
        _reject_nul(value, f"The value of environment variable {name!r}")
        entries.append(f"{name}={value}")
    if not entries:
        return NUL
    return "".join(entry + NUL for entry in entries)


def native_thread_id() -> int:
    """The kernel thread id `OpenThread` needs.

    `Thread.ident` is a Python-level cookie with no OS meaning, so it cannot be used here.
    Wrapped in a function of its own so a failure before publication can be injected without
    patching the threading module the test runner also uses.
    """
    return threading.get_native_id()


def reply_resolution(on_complete: Callable[[Optional[str]], None]) -> ResolveCallback:
    """Maps one queue resolution onto the responder's delivered / not-delivered contract."""

    def resolved(disposition: InputDisposition, error: Optional[BaseException]) -> None:
        if error is not None:
            on_complete(f"{REASON_WRITE_FAILED}: {error!r}")
        elif disposition is InputDisposition.ACCEPTED:
            on_complete(None)
        else:
            on_complete(f"{REASON_DISCARDED} ({disposition.value})")

    return resolved


# ------------------------------------------------------------------- input queue


class InputLane(Enum):
    """Which lane an item is admitted to. Control items are serviced ahead of data."""

    DATA = "data"
    CONTROL = "control"


class Receipt:
    """Resolution of one queued item. Resolved exactly once, by whoever retires it.

    `on_resolve` lets a producer observe that transition without ever waiting for it, which
    is what the output reader needs when it is the producer.
    """

    def __init__(self, on_resolve: Optional[ResolveCallback] = None) -> None:
        self._lock = threading.Lock()
        self._event = threading.Event()
        self.error: Optional[BaseException] = None
        self.disposition: Optional[InputDisposition] = None
        # Two counters, because they answer two different questions: how many resolutions
        # took effect (never more than one), and how many were attempted (a caller retiring
        # an item twice is a bug worth failing a test on).
        self.resolutions = 0
        self.attempts = 0
        self._on_resolve = on_resolve

    def resolve(self, disposition: InputDisposition, error: Optional[BaseException] = None) -> None:
        with self._lock:  # the check and the set are one step, so two threads cannot both win
            self.attempts += 1
            if self._event.is_set():
                return
            self.disposition = disposition
            self.error = error
            self.resolutions += 1
            self._event.set()
            callback = self._on_resolve
        if callback is not None:  # outside the lock: a callback must not be able to re-enter it
            try:
                callback(disposition, error)
            except BaseException as exc:  # a completion callback must never strand the queue
                console.debug(f"input completion callback raised: {exc!r}")

    @property
    def resolved(self) -> bool:
        return self._event.is_set()

    @property
    def delivered(self) -> bool:
        return self.resolved and self.disposition is InputDisposition.ACCEPTED and self.error is None


class InputItem:
    """One whole logical write, plus the cursor the writer keeps across partial writes.

    An urgent control item also carries the preemption generation it was posted under, so
    the writer acknowledges at least that generation before it starts writing the item.
    """

    def __init__(
        self,
        data: bytes,
        receipt: Receipt,
        lane: InputLane,
        sequence: int,
        stop: bool = False,
        generation: int = 0,
    ) -> None:
        self.data = data
        self.receipt = receipt
        self.lane = lane
        self.sequence = sequence
        self.stop = stop
        self.generation = generation
        self.cursor = 0


class InputQueue:
    """Bounded, byte-accounted queue with a reserved admission partition and a control lane.

    Dequeue is not completion: the item under the writer's cursor stays accounted for and
    keeps its receipt attached until its last byte completes or teardown fails it, so
    capacity is released exactly once at that terminal transition.
    """

    def __init__(
        self,
        max_item_bytes: int = MAX_INPUT_ITEM_BYTES,
        max_pending_bytes: int = MAX_PENDING_INPUT_BYTES,
        reserved_bytes: int = RESERVED_INPUT_BYTES,
        max_pending_items: int = MAX_PENDING_INPUT_ITEMS,
        reserved_items: int = RESERVED_INPUT_ITEMS,
    ) -> None:
        self._condition = threading.Condition()
        self._data: Deque[InputItem] = collections.deque()
        self._control: Deque[InputItem] = collections.deque()
        self._current: Optional[InputItem] = None
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
        lane: InputLane = InputLane.DATA,
        on_resolve: Optional[ResolveCallback] = None,
        generation: int = 0,
    ) -> Tuple[InputWriteResult, Receipt]:
        """One non-blocking whole-item admission. Never waits, whoever the producer is."""
        receipt = Receipt(on_resolve)
        size = len(data)
        enqueued = False
        with self._condition:
            byte_budget, item_budget = self._budget(reserved)
            queued = len(self._data) + len(self._control) + (0 if self._current is None else 1)
            if not self._accepting:
                result = InputWriteResult(InputDisposition.CLOSED, 0)
            elif size == 0:
                # Nothing to deliver, so it never becomes an entry: an empty item would grow
                # the queue without ever touching the byte budget.
                result = InputWriteResult(InputDisposition.ACCEPTED, 0)
            elif size > self._max_item_bytes:
                result = InputWriteResult(InputDisposition.BACKPRESSURE, 0)
            elif self._pending_bytes + size > byte_budget or queued >= item_budget:
                result = InputWriteResult(InputDisposition.BACKPRESSURE, 0)
            else:
                self._append(InputItem(bytes(data), receipt, lane, self._next_sequence(), generation=generation))
                self._pending_bytes += size
                result = InputWriteResult(InputDisposition.ACCEPTED, size)
                enqueued = True
        if not enqueued:  # nothing will retire it later, so it resolves here
            receipt.resolve(result.disposition)
        return result, receipt

    def post_stop(self) -> Receipt:
        """Teardown's own sentinel. Admitted after the queue stops accepting producers."""
        receipt = Receipt()
        with self._condition:
            self._append(InputItem(b"", receipt, InputLane.CONTROL, self._next_sequence(), stop=True))
        return receipt

    def _next_sequence(self) -> int:
        self._sequence += 1
        return self._sequence

    def _append(self, item: InputItem) -> None:
        """Called under the condition. Appending is what wakes a parked writer."""
        if item.lane is InputLane.CONTROL:
            self._control.append(item)
        else:
            self._data.append(item)
        self._condition.notify_all()

    def _budget(self, reserved: bool) -> Tuple[int, int]:
        if reserved:
            return self._max_pending_bytes, self._max_pending_items
        return self._max_pending_bytes - self._reserved_bytes, self._max_pending_items - self._reserved_items

    def next_item(self, timeout: float) -> Optional[InputItem]:
        """The item under the cursor, waiting up to `timeout` for one to arrive.

        Control items are serviced ahead of data; order inside a lane is FIFO.
        """
        with self._condition:
            if self._current is None and not self._control and not self._data:
                self._condition.wait(timeout)
            if self._current is None:
                if self._control:
                    self._current = self._control.popleft()
                elif self._data:
                    self._current = self._data.popleft()
            return self._current

    def current(self) -> Optional[InputItem]:
        with self._condition:
            return self._current

    def retire_current(self, delivered: bool, error: Optional[BaseException] = None) -> None:
        """Releases the item's accounting once and resolves its receipt once."""
        with self._condition:
            item = self._current
            if item is None:
                return
            self._current = None
            self._pending_bytes -= len(item.data)
        item.receipt.resolve(
            InputDisposition.ACCEPTED if delivered and error is None else InputDisposition.CLOSED, error
        )

    def requeue_current_front(self) -> None:
        """Returns an untouched item to the head of its lane, accounting unchanged."""
        with self._condition:
            item = self._current
            if item is None:
                return
            self._current = None
            if item.lane is InputLane.CONTROL:
                self._control.appendleft(item)
            else:
                self._data.appendleft(item)
            self._condition.notify_all()

    def stop_accepting(self) -> None:
        with self._condition:
            self._accepting = False

    def accepting(self) -> bool:
        with self._condition:
            return self._accepting

    def has_pending(self) -> bool:
        with self._condition:
            return self._current is not None or bool(self._control) or bool(self._data)

    def pending_bytes(self) -> int:
        with self._condition:
            return self._pending_bytes

    def pending_items(self) -> int:
        with self._condition:
            return len(self._data) + len(self._control) + (0 if self._current is None else 1)

    def discard_pending_data(self) -> List[InputItem]:
        """Drops queued data items, resolving each receipt as not delivered.

        The item under the cursor is left alone: it may be inside a synchronous write, and
        only the writer can retire it.
        """
        with self._condition:
            items = list(self._data)
            self._data.clear()
            for item in items:
                self._pending_bytes -= len(item.data)
        for item in items:
            item.receipt.resolve(InputDisposition.CLOSED)
        return items

    def close_and_fail_all(self, error: Optional[BaseException] = None) -> List[InputItem]:
        with self._condition:
            self._accepting = False
            items = list(self._control) + list(self._data)
            self._control.clear()
            self._data.clear()
            if self._current is not None:
                items.append(self._current)
                self._current = None
            self._pending_bytes = 0
        for item in items:  # callbacks run outside the lock and cannot re-enter the queue
            try:
                item.receipt.resolve(InputDisposition.CLOSED, error)
            except BaseException as exc:  # a receipt must never strand its siblings
                console.debug(f"input receipt callback raised: {exc!r}")
        return items


# ------------------------------------------------------------------- input writer


class WriteAborted(Exception):
    """A synchronous write completed as cancelled.

    `WriteFile` initializes its byte count to zero and a cancelled completion carries no
    trustworthy cursor, so the item it belonged to is retired rather than retried.
    """


class WriteChannel:
    """The two native operations the writer performs, behind one seam.

    `cancel()` is issued from another thread against the writer's own thread handle, which
    is why the writer never derives that handle itself.
    """

    def write(self, data: bytes) -> int:
        raise NotImplementedError

    def cancel(self) -> None:
        raise NotImplementedError


class GateDecision(Enum):
    RUN = "run"
    ABORT = "abort"


class DecisionGate:
    """A gate carrying a decision, so a writer released without a stored cancel handle exits
    instead of blocking in a write nothing can cancel."""

    def __init__(self) -> None:
        self._event = threading.Event()
        self._decision = GateDecision.ABORT

    def set(self, decision: GateDecision) -> None:
        self._decision = decision
        self._event.set()

    def wait(self, timeout: Optional[float] = None) -> GateDecision:
        self._event.wait(timeout)
        return self._decision

    @property
    def released(self) -> bool:
        return self._event.is_set()


class _DropLog:
    """Rate-limited loss reporting: a query storm must not turn the log into its own flood."""

    def __init__(self, interval: float = DROP_LOG_INTERVAL_SECONDS) -> None:
        self._interval = interval
        self._lock = threading.Lock()
        self._last = 0.0
        self.dropped = 0

    def record(self, reason: str) -> None:
        with self._lock:
            self.dropped += 1
            now = time.monotonic()
            if self._last and now - self._last < self._interval:
                return
            self._last = now
            dropped = self.dropped
        console.debug(f"terminal input item not delivered ({reason}); {dropped} lost so far")


class InputWriter:
    """Sole owner of every write to the pseudoconsole's input pipe.

    Startup is a gate protocol: the thread publishes its native id and parks, the creator
    opens a thread handle while the gate still holds it, stores the handle, and releases the
    gate with `RUN`. Any failure in between releases the gate with `ABORT`, and a writer that
    wakes to `ABORT` returns without touching the pipe.
    """

    def __init__(self, queue: InputQueue, channel: WriteChannel, name: str = "codeplain-conpty-writer") -> None:
        self.queue = queue
        self.channel = channel
        self.ready = threading.Event()
        self.finished = threading.Event()
        self.gate = DecisionGate()
        self.failed = threading.Event()
        self.exc: Optional[BaseException] = None
        self.native_id: Optional[int] = None
        self.cancels = 0
        self.drops = _DropLog()
        self._stopping = threading.Event()
        self._lock = threading.Lock()  # guards both generations, held across check and cancel
        self._requested_generation = 0
        self._preempted_generation = 0
        self.thread = threading.Thread(target=self._run, name=name, daemon=True)

    # ------------------------------------------------------------ creator side

    def start(self) -> None:
        self.thread.start()

    def started(self) -> bool:
        return self.thread.ident is not None

    def await_ready(self, deadline: float, stop_check: Optional[Callable[[], None]] = None) -> Optional[int]:
        """Waits for the writer to publish its native id or its failure, under a deadline.

        Returns the id, or None when the writer failed or the deadline expired. The wait is
        bounded and abortable because a writer that dies before publishing must not park the
        creator. `stop_check` runs at least once even when the writer is already ready, so a
        cancellation set while it was starting is not skipped.
        """
        while True:
            if stop_check is not None:
                stop_check()
            if self.ready.is_set():
                return None if self.failed.is_set() else self.native_id
            if time.monotonic() >= deadline:
                return None
            self.ready.wait(CANCEL_TICK_SECONDS)

    def deliver_control(self, data: bytes, deadline_seconds: float) -> bool:
        """Posts an urgent control item, preempts any data write, and awaits its receipt.

        Reserved capacity buys admission, not service: a writer already blocked in a
        synchronous data write never reaches the queue again on its own, so the in-flight
        write is cancelled through the stored thread handle until the writer acknowledges
        this generation.

        The generation is published under the same lock that makes the item visible. Bumping
        it afterwards would let an idle writer dequeue the item, acknowledge the previous
        generation and enter its control write before this thread starts cancelling — which
        is exactly the wrong-call cancellation the lock exists to prevent.
        """
        with self._lock:
            self._requested_generation += 1
            generation = self._requested_generation
            result, receipt = self.queue.submit(data, reserved=True, lane=InputLane.CONTROL, generation=generation)
            if result.disposition is not InputDisposition.ACCEPTED:
                # Nothing became visible, so the request is withdrawn rather than left for
                # the writer to acknowledge against an item that does not exist.
                self._requested_generation = generation - 1
                return False
        deadline = time.monotonic() + deadline_seconds
        while not receipt.resolved:
            if time.monotonic() >= deadline:
                return False
            if self.finished.is_set() and not receipt.resolved:
                break  # a retired writer resolves every receipt, so this is a lost race, not a wait
            with self._lock:
                # The lock spans the check and the cancel, so a cancel can never land on the
                # control write the acknowledgment has just cleared the way for.
                if self._preempted_generation < generation:
                    self._cancel()
            time.sleep(CANCEL_TICK_SECONDS)
        return receipt.delivered

    def stop(self, bound_seconds: float = WRITER_JOIN_DEADLINE_SECONDS) -> bool:
        """Sentinel, discard, retried cancel, bounded join. False when the writer is still in a write.

        An idle writer is parked on the queue rather than inside a write, so a cancel-only
        loop would report ERROR_NOT_FOUND forever and never join it.
        """
        self._stopping.set()
        self.queue.stop_accepting()
        self.queue.discard_pending_data()
        self.queue.post_stop()
        if not self.started():
            return True
        if not self.gate.released:  # an unreleased gate parks the writer forever
            self.gate.set(GateDecision.ABORT)
        deadline = time.monotonic() + bound_seconds
        while True:
            self.thread.join(CANCEL_TICK_SECONDS)
            if not self.thread.is_alive():
                return True
            if time.monotonic() >= deadline:
                return False
            with self._lock:
                self._cancel()

    def _cancel(self) -> None:
        """Called under the preemption lock, by whoever is waiting on the writer."""
        self.cancels += 1
        try:
            self.channel.cancel()
        except BaseException as exc:  # cancellation is best effort; the bound decides the outcome
            console.debug(f"cancelling the terminal input write raised: {exc!r}")

    # ------------------------------------------------------------- writer thread

    def _run(self) -> None:
        try:
            try:
                self.native_id = native_thread_id()
            finally:
                # From the writer's own finally, so a writer that dies before publishing the
                # id still releases the creator.
                self.ready.set()
            if self.gate.wait() is not GateDecision.RUN:
                return
            self._loop()
        except BaseException as exc:  # nothing here reaches threading.excepthook
            self._publish(exc)
        finally:
            self.queue.close_and_fail_all()
            self.finished.set()

    def _publish(self, exc: BaseException) -> None:
        self.exc = exc  # stored while still unobservable
        self.failed.set()

    def _loop(self) -> None:
        while True:
            item = self.queue.next_item(WRITER_IDLE_TICK_SECONDS)
            if item is None:
                if self._stopping.is_set():
                    return
                continue
            if item.stop:
                self.queue.retire_current(delivered=True)
                return
            self._service(item)
            if self._stopping.is_set():
                # Consulted before taking another item, so a cancelled write during teardown
                # exits instead of consuming the backlog.
                return

    def _service(self, item: InputItem) -> None:
        if item.lane is InputLane.CONTROL:
            # Published before the control write begins: it means "no earlier data I/O
            # remains", and it is what stops the poster's cancel loop. The item's own
            # generation is the floor, so the acknowledgment can never be older than the
            # request that produced the item.
            self._acknowledge_preemption(item.generation)
            self._write_item(item, preemptible=False)
            return
        self._write_item(item, preemptible=True)

    def _write_item(self, item: InputItem, preemptible: bool) -> None:
        while item.cursor < len(item.data):
            if preemptible and self._control_pending():
                if item.cursor == 0:  # nothing was written, so nothing can be lost or duplicated
                    self.queue.requeue_current_front()
                else:
                    self._retire_undelivered(item, "preempted mid-item")
                self._acknowledge_preemption()
                return
            try:
                written = self.channel.write(item.data[item.cursor :])
            except WriteAborted:
                expected = self._stopping.is_set() or self._control_pending()
                self._retire_undelivered(item, "write cancelled")
                self._acknowledge_preemption()
                if not expected:
                    # A cancellation nobody asked for is a genuine writer failure; one the
                    # stop protocol or a preemption asked for is control flow.
                    raise
                return
            except BaseException as exc:
                self.queue.retire_current(delivered=False, error=exc)
                raise
            item.cursor += written
        self.queue.retire_current(delivered=True)

    def _retire_undelivered(self, item: InputItem, reason: str) -> None:
        self.queue.retire_current(delivered=False)
        if item.lane is InputLane.DATA:
            self.drops.record(reason)

    def _control_pending(self) -> bool:
        with self._lock:
            return self._preempted_generation < self._requested_generation

    def _acknowledge_preemption(self, at_least: int = 0) -> None:
        with self._lock:
            self._preempted_generation = max(self._preempted_generation, self._requested_generation, at_least)

    @property
    def acknowledged_generation(self) -> int:
        with self._lock:
            return self._preempted_generation
