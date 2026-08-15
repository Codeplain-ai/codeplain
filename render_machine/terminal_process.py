"""Platform-neutral terminal-process interface, shared constants, and backend dispatch.

A `TerminalProcess` runs one command with a terminal behind all three of its standard
descriptors and owns every handle that arrangement needs. The POSIX implementation lives
in `render_machine._posix_pty`; the Windows ConPTY implementation will live in
`render_machine._conpty`. Only this module is imported by callers.
"""

import sys
import threading
from dataclasses import dataclass
from enum import Enum
from typing import List, Optional, Sequence, Tuple

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
INPUT_WRITE_BUDGET_BYTES = 64 * 1024

# Head and tail retained from the launcher's stderr, so a flooding launcher cannot hand
# the parent an unbounded buffer while the reads continue.
LAUNCHER_STDERR_CAP_BYTES = 16 * 1024


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
    """

    reader_failed: threading.Event
    reader_exc: Optional[BaseException]

    def spawn(
        self,
        command: Sequence[str],
        cwd: Optional[str] = None,
        env: Optional[dict] = None,
        terminal_size: Tuple[int, int] = (TERMINAL_COLUMNS, TERMINAL_ROWS),
        stop_event: Optional[threading.Event] = None,
        input_driver: Optional[object] = None,
    ) -> None:
        raise NotImplementedError

    def poll(self) -> Optional[int]:
        """Non-blocking exit status, or None while the target runs. Reaps on completion."""
        raise NotImplementedError

    def read_output(self) -> str:
        """Decoded output accumulated since the previous call."""
        raise NotImplementedError

    def read_raw_output(self) -> bytes:
        """Raw output bytes accumulated since the previous call."""
        raise NotImplementedError

    def write_input(self, data: bytes) -> InputWriteResult:
        raise NotImplementedError

    def terminate_tree(self, grace: float = SIGTERM_GRACE_PERIOD_SECONDS) -> None:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError

    def __enter__(self) -> "TerminalProcess":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()


def create_terminal_process() -> TerminalProcess:
    """Returns the backend for the running platform."""
    if sys.platform == "win32":
        raise TerminalEnvironmentError("The ConPTY backend is not implemented yet.")

    from render_machine._posix_pty import PosixPtyProcess

    return PosixPtyProcess()


def available_backends() -> List[str]:
    """Names the backends this build can construct. Used by diagnostics and tests."""
    return [] if sys.platform == "win32" else ["posix-pty"]
