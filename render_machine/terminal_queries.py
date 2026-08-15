"""Platform-neutral state for the terminal queries the reader answers live.

A target under `TERM=xterm-256color` may emit a device-status, cursor-position or
device-attributes query and block until the terminal replies. A real terminal always
answers, so the parser has to run in the reader rather than after the fact — and the reply
has to be admitted without the reader ever waiting, since the reader is the only drainer of
the target's output.

The responder owns the obligation that admission creates, for the item's whole lifecycle:

* While `ACTIVE`, a query performs exactly one non-blocking whole-item admission and
  registers an obligation. Immediate pressure, a native write failure and a teardown
  discard all resolve it as not delivered and record `kind` plus `reason`.
* The foreground switches the responder to `QUIESCED` as soon as it observes an execution
  outcome, before stopping either input pump. A query first seen after that renders but
  records nothing: there is no client left whose query can be answered.
* Obligations registered while `ACTIVE` keep reporting, even when teardown is what
  discovers the failure.

One lock linearizes the query callback with the `ACTIVE -> QUIESCED` transition, so a
callback either admits while active or observes quiescence — never both, and never neither.
It is reentrant because an admission that is rejected outright resolves its obligation
inside the same call. Completion callbacks update the recorded failures through this same
state but never invoke backend code while holding the lock.

This is separate from a reader or writer failure: the pumps can be healthy while one
required protocol response could not be accepted.
"""

import functools
import threading
from dataclasses import dataclass
from enum import Enum
from typing import Callable, List, Optional, Set

# Reasons a reply can fail to reach the target.
REASON_ADMISSION_RAISED = "admission raised"
REASON_DISCARDED = "discarded before delivery"
REASON_WRITE_FAILED = "write failed"

# A backend admission: hands the reply over without blocking, then resolves the completion
# callback with None when the last native byte lands, or with a reason when it cannot.
CompletionCallback = Callable[[Optional[str]], None]
AdmitReply = Callable[[bytes, CompletionCallback], None]


class ResponderState(Enum):
    ACTIVE = "active"
    QUIESCED = "quiesced"


@dataclass(frozen=True)
class TerminalReplyFailure:
    kind: str
    reason: str

    def __str__(self) -> str:
        return f"{self.kind} reply {self.reason}"


class _Obligation:
    """One admitted reply, resolved exactly once by whoever retires it."""

    __slots__ = ("kind", "resolved")

    def __init__(self, kind: str) -> None:
        self.kind = kind
        self.resolved = False


class TerminalQueryResponder:
    """Tracks the delivery obligation of every terminal reply the parser produces.

    A responder built without an admission callable — the legacy backend, which has no
    input channel — starts quiesced, so a printed escape query creates no obligation.
    """

    def __init__(self, admit: Optional[AdmitReply] = None, active: bool = True) -> None:
        self._lock = threading.RLock()
        self._admit = admit
        self._state = ResponderState.ACTIVE if active and admit is not None else ResponderState.QUIESCED
        self._outstanding: Set[_Obligation] = set()
        self._failures: List[TerminalReplyFailure] = []
        self.admitted = 0
        self.render_only = 0

    @property
    def state(self) -> ResponderState:
        with self._lock:
            return self._state

    @property
    def reply_failed(self) -> bool:
        with self._lock:
            return bool(self._failures)

    @property
    def failures(self) -> List[TerminalReplyFailure]:
        with self._lock:
            return list(self._failures)

    @property
    def outstanding(self) -> int:
        with self._lock:
            return len(self._outstanding)

    def failure_detail(self) -> str:
        return "; ".join(str(failure) for failure in self.failures)

    def quiesce(self) -> None:
        """Idempotent, foreground-triggered. Outstanding obligations keep reporting."""
        with self._lock:
            self._state = ResponderState.QUIESCED

    def answer(self, kind: str, payload: bytes) -> None:
        """The parser's reply hook, called on the reader thread. Never waits, never raises."""
        with self._lock:
            if self._state is ResponderState.QUIESCED or self._admit is None:
                self.render_only += 1
                return
            obligation = _Obligation(kind)
            self._outstanding.add(obligation)
            self.admitted += 1
            try:
                self._admit(payload, functools.partial(self._resolve, obligation))
            except BaseException as exc:
                self._resolve(obligation, f"{REASON_ADMISSION_RAISED} {exc!r}")

    def _resolve(self, obligation: _Obligation, reason: Optional[str]) -> None:
        with self._lock:
            if obligation.resolved:
                return
            obligation.resolved = True
            self._outstanding.discard(obligation)
            if reason is not None:
                self._failures.append(TerminalReplyFailure(obligation.kind, reason))
