"""Platform-neutral terminal-process interface, shared constants, and backend dispatch.

A `TerminalProcess` runs one command with a terminal behind all three of its standard
descriptors and owns every handle that arrangement needs. The POSIX implementation lives
in `render_machine._posix_pty` and the Windows ConPTY implementation in
`render_machine._conpty`. Only this module is imported by callers.
"""

import importlib
import os
import sys
import threading
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, List, Optional, Sequence, Tuple

from plain2code_console import console
from plain2code_exceptions import RenderCancelledError

if TYPE_CHECKING:  # both import this module at runtime, so neither may be imported here
    from render_machine.output_normalizer import OutputNormalizer
    from render_machine.terminal_queries import TerminalQueryResponder

# Override for environments where PTY allocation fails: set CODEPLAIN_NO_PTY=1 to run
# scripts on the legacy pipe backend. Never selected automatically - a failed openpty() is
# an environment error, and downgrading silently would make execution behaviour depend on
# the machine again. Uses of this variable are worth reporting; it goes away with the
# legacy path.
NO_PTY_ENV_VAR = "CODEPLAIN_NO_PTY"
NO_PTY_ENABLED_VALUE = "1"

# Launch, reader, and writer infrastructure failures surface on the renderer's existing
# environment-error channel rather than being handed to the LLM patcher as a test failure.
ENVIRONMENT_ERROR_EXIT_CODE = 69

# The terminal the child sees. Fixed rather than inherited: execution behaviour must not
# depend on the size of the window Codeplain happens to be running in.
TERMINAL_COLUMNS = 120
TERMINAL_ROWS = 40

DEFAULT_TERM = "xterm-256color"

# Every duration below is a monotonic budget, never wall time.
HANDSHAKE_TIMEOUT_SECONDS = 20.0
SIGTERM_GRACE_PERIOD_SECONDS = 3.0
# Bounds the delivery of a graceful control byte, which on Windows travels through a
# synchronous pipe a wedged target may never read. It is never the grace period itself:
# queue delay must not silently consume the handler's time.
CONTROL_DELIVERY_DEADLINE_SECONDS = 2.0
GRACE_TICK_SECONDS = 0.05
REAP_DEADLINE_SECONDS = 5.0
DRAIN_DEADLINE_SECONDS = 2.0
DRAIN_QUIET_PERIOD_SECONDS = 0.1
POLL_INTERVAL_SECONDS = 0.05

# The final drain is bounded by bytes as well as by time: a descendant that escaped the
# process group can keep the master readable forever.
DRAIN_MAX_BYTES = 4 * 1024 * 1024
READ_CHUNK_BYTES = 65536

# Bounds on the ordered input queue. Reserved capacity is an admission partition for
# spawn/control items, never a way to jump the FIFO order.
MAX_INPUT_ITEM_BYTES = 64 * 1024
MAX_PENDING_INPUT_BYTES = 256 * 1024
RESERVED_INPUT_BYTES = 8 * 1024
# The queue is bounded in items as well as in bytes: a queue entry costs far more than
# the bytes it carries, so the byte budget alone does not bound small items.
MAX_PENDING_INPUT_ITEMS = 1024
RESERVED_INPUT_ITEMS = 64
INPUT_WRITE_BUDGET_BYTES = 64 * 1024

# Head and tail retained from the launcher's stderr, so a flooding launcher cannot hand
# the parent an unbounded buffer while the reads continue.
LAUNCHER_STDERR_CAP_BYTES = 16 * 1024

# Published when close() has waited out its bound and the reader is still running: such a
# reader can still append output or fail afterwards, so the transcript it produced cannot
# be trusted and the execution is an environment failure.
READER_STALL_DETAIL = "the terminal output reader did not terminate within its shutdown bound"

# The two owners a descriptor bundle can have. One field carrying one of these is what
# keeps a rollback and a reader from ever disagreeing about who releases what.
OWNER_PARENT = "parent"
OWNER_READER = "reader"

# What a timeout diagnostic says about the input the target was given. The spawn-time
# end-of-file is best-effort by nature: a program that flushes or reconfigures its
# terminal before reading — getpass's TCSAFLUSH, a curses initialization — discards the
# queued byte and then blocks on input nothing will send. ConPTY, which cannot deliver
# an end-of-file at all, states its own note instead.
NO_INPUT_NOTE = (
    " An end-of-file was queued at the script's terminal at spawn and re-delivered while "
    "the target stayed quiet; a program still waiting after that is blocked on something "
    "other than the input it was given."
)


class InputDisposition(Enum):
    """Immediate whole-item backend admission — never a delivery receipt."""

    ACCEPTED = "accepted"
    BACKPRESSURE = "backpressure"
    CLOSED = "closed"


@dataclass(frozen=True)
class InputWriteResult:
    disposition: InputDisposition
    accepted_bytes: int


class TerminalProcessError(Exception):
    """Base class for failures the terminal backend reports to the renderer."""


class TerminalEnvironmentError(TerminalProcessError):
    """Infrastructure failure — reported on the environment-error channel."""

    exit_code = ENVIRONMENT_ERROR_EXIT_CODE


class TerminalLaunchError(TerminalEnvironmentError):
    """The launcher never reached the target command."""


class TerminalReaderError(TerminalEnvironmentError):
    """The output reader failed, so the target's output is no longer being drained."""


class TerminalProcess:
    """Interface implemented by every backend.

    `spawn()` is bounded and cancellable; `close()` is idempotent and releases every
    handle the backend owns. Instances are single-use.

    Output accumulation is identical on every backend — one lock over a decoded list and a
    raw buffer, fed by whatever read loop the backend runs — so it is implemented here
    rather than three times over. A backend supplies its read loop, its normalizer and its
    query responder, calls `super().__init__()` before either, and inherits the rest.
    """

    normalizer: "OutputNormalizer"
    query_responder: "TerminalQueryResponder"

    def __init__(self) -> None:
        self.reader_failed = threading.Event()
        self.reader_exc: Optional[BaseException] = None
        self._stop_event = threading.Event()

        self._output_lock = threading.Lock()
        self._decoded: List[str] = []
        self._raw = bytearray()

    def spawn(
        self,
        command: Sequence[str],
        cwd: Optional[str] = None,
        env: Optional[dict] = None,
        terminal_size: Tuple[int, int] = (TERMINAL_COLUMNS, TERMINAL_ROWS),
        stop_event: Optional[threading.Event] = None,
    ) -> None:
        raise NotImplementedError

    def poll(self) -> Optional[int]:
        """Non-blocking exit status, or None while the target runs. Reaps on completion."""
        raise NotImplementedError

    def read_output(self) -> str:
        """Decoded output accumulated since the previous call."""
        with self._output_lock:
            text = "".join(self._decoded)
            self._decoded.clear()
            return text

    def read_raw_output(self) -> bytes:
        """Raw output bytes accumulated since the previous call."""
        with self._output_lock:
            data = bytes(self._raw)
            self._raw.clear()
            return data

    def normalized_output(self) -> str:
        """The rendered transcript so far. Cumulative, unlike `read_output()`."""
        return self.normalizer.text()

    @property
    def terminal_reply_failed(self) -> bool:
        """True when a reply the target was waiting for could not be delivered.

        Independent of `reader_failed`: both pumps can be healthy while one required
        protocol response was never accepted.
        """
        return self.query_responder.reply_failed

    def terminal_reply_detail(self) -> str:
        """Query kinds and pressure reasons behind `terminal_reply_failed`."""
        return self.query_responder.failure_detail()

    def _feed_output(self, chunk: bytes, decoder) -> None:
        """The one entry point every read loop hands its bytes to."""
        text = decoder.decode(chunk)
        with self._output_lock:
            self._raw += chunk
            if text:
                self._decoded.append(text)
        self.normalizer.feed(chunk)  # outside the output lock: parsing must not block read_output()

    def _flush_decoder(self, decoder) -> None:
        tail = decoder.decode(b"", final=True)  # a trailing partial sequence becomes U+FFFD
        if tail:
            with self._output_lock:
                self._decoded.append(tail)

    def _check_cancelled(self) -> None:
        if self._stop_event.is_set():
            raise RenderCancelledError()

    def write_input(self, data: bytes) -> InputWriteResult:
        raise NotImplementedError

    def resize(self, columns: int, rows: int) -> None:
        """Applies a new terminal size to the live target and the rendering parser."""
        raise NotImplementedError

    def terminate_tree(self, grace: float = SIGTERM_GRACE_PERIOD_SECONDS) -> None:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError

    def no_input_note(self) -> str:
        """What a timeout diagnostic says about the end-of-file this backend can give.

        A backend that gives the target end-of-file at spawn needs nothing beyond the
        default; one that cannot says so itself. The note belongs to the backend that ran,
        not to the platform the renderer is on: under the escape hatch on Windows the pipe
        backend delivers end-of-file immediately, and a note keyed on `sys.platform` would
        describe a backend that never ran.
        """
        return NO_INPUT_NOTE

    def infrastructure_failure(self) -> Optional[str]:
        """Detail of a failed backend pump, or None while they are all healthy.

        The output reader is the one pump every backend has. A backend that runs more of
        them — the Windows input writer — reports them here too, so the execution loop has
        one question to ask rather than one per platform.
        """
        if self.reader_failed.is_set():
            return f"the terminal output reader failed: {self.reader_exc!r}"
        return None

    def _publish_reader_stall(self) -> None:
        """Publishes a reader that close() could not join, and refuses to return quietly.

        A backend whose reader is still running owns handles it has not released and can
        still append output, so close() must not report a released backend: the stall is
        published on the reader's own channel and raised.
        """
        error = TerminalReaderError(READER_STALL_DETAIL)
        if self.reader_exc is None:
            self.reader_exc = error
        self.reader_failed.set()
        raise error

    def __enter__(self) -> "TerminalProcess":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()


def pty_disabled_by_environment() -> bool:
    """Reads the override from Codeplain's own environment, once per spawn.

    Only the exact value "1" selects the pipe backend; unset, empty, or anything else
    leaves the PTY in place. Env-only, so it stays a break-glass control rather than a
    configuration axis a workflow can be built on.
    """
    return os.environ.get(NO_PTY_ENV_VAR) == NO_PTY_ENABLED_VALUE


def child_environment(env: Optional[dict]) -> dict:
    """The environment a target runs in, minus the controls it must not observe.

    A rendered script that could see the override could branch on it, which would turn a
    support control into part of the contract.
    """
    child_env = dict(os.environ if env is None else env)
    child_env.pop(NO_PTY_ENV_VAR, None)
    return child_env


def terminal_child_environment(env: Optional[dict]) -> dict:
    """`child_environment()` plus the policy a target with a terminal of its own runs under.

    TERM is declared rather than inherited, so a toolchain's rendering does not depend on
    the terminal Codeplain happens to be running in. GIT_TERMINAL_PROMPT is cleared because
    git reads the terminal directly — /dev/tty on POSIX, the console on Windows — so neither
    a synthetic end-of-file nor a redirected stdin can reach a credential prompt, and failing
    is the only bounded outcome.
    """
    child_env = child_environment(env)
    term = child_env.get("TERM")
    child_env["TERM"] = term if term else DEFAULT_TERM
    child_env["GIT_TERMINAL_PROMPT"] = "0"
    return child_env


# Every backend module, each publishing the teardown budget its own constants add up to.
# A module whose platform this is not refuses to import, which is what keeps the budget
# below a question about this machine rather than about the codebase.
_BACKEND_MODULES = ("render_machine._legacy_pipe", "render_machine._posix_pty", "render_machine._conpty")


def teardown_budget_seconds() -> float:
    """The longest teardown any backend reachable on this platform may spend.

    A caller that waits for a render to stop has to outlast it. The three pipelines are
    different lengths — the ConPTY one is much the longest — so a wait assembled from the
    POSIX constants would report a render that did not stop while the backend was still
    inside the bound its own constants grant it.
    """
    budgets = []
    for module_name in _BACKEND_MODULES:
        try:
            module = importlib.import_module(module_name)
        except ImportError:  # this backend is not built on this platform
            continue
        budgets.append(module.TEARDOWN_BUDGET_SECONDS)
    return max(budgets)


def create_terminal_process() -> TerminalProcess:
    """The one construction site: returns the backend this execution runs on."""
    if pty_disabled_by_environment():
        console.warning(
            f"{NO_PTY_ENV_VAR}={NO_PTY_ENABLED_VALUE} is set, so this script runs on the legacy pipe "
            "backend: terminal semantics are disabled and isatty() will be false in the script. "
            "Unset it once the environment problem that needed it is resolved, and please report that problem."
        )
        from render_machine._legacy_pipe import LegacyPipeProcess

        return LegacyPipeProcess()

    if sys.platform == "win32":
        from render_machine._conpty import ConPtyProcess

        return ConPtyProcess()

    from render_machine._posix_pty import PosixPtyProcess

    return PosixPtyProcess()
