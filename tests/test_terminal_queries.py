"""Tests for the live terminal query responder.

The responder cases are platform-neutral and run everywhere. The backend cases spawn a real
target on a real pseudoterminal, so they are POSIX-only; every boundary they assert is
driven through a hook, never through a sleep.
"""

import sys
import threading
import time
from pathlib import Path

import pytest

from render_machine.output_normalizer import QUERY_CURSOR_POSITION, QUERY_DEVICE_ATTRIBUTES, QUERY_DEVICE_STATUS
from render_machine.terminal_process import InputDisposition, InputWriteResult
from render_machine.terminal_queries import (
    MAX_TRACKED_FAILURES,
    ResponderState,
    TerminalQueryResponder,
    reply_resolution,
)

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="The POSIX PTY backend is not built on Windows.")

if sys.platform != "win32":
    from render_machine import _posix_pty

SPAWN_TIMEOUT = 20.0


class _Admissions:
    """Records every admission and hands back the completion callback."""

    def __init__(self, immediate_reason=None, raises=None):
        self.payloads = []
        self.completions = []
        self._immediate_reason = immediate_reason
        self._raises = raises

    def __call__(self, payload, on_complete):
        self.payloads.append(payload)
        if self._raises is not None:
            raise self._raises
        if self._immediate_reason is not None:
            on_complete(self._immediate_reason)
            return
        self.completions.append(on_complete)


def test_a_responder_without_an_input_channel_starts_quiesced():
    """The legacy backend has nowhere to write a reply, so a query creates no obligation."""
    responder = TerminalQueryResponder()

    responder.answer(QUERY_CURSOR_POSITION, b"\x1b[1;1R")

    assert responder.state is ResponderState.QUIESCED
    assert responder.reply_failed is False
    assert responder.render_only == 1
    assert responder.admitted == 0


def test_an_admitted_reply_that_completes_leaves_no_failure():
    admissions = _Admissions()
    responder = TerminalQueryResponder(admissions)

    responder.answer(QUERY_CURSOR_POSITION, b"\x1b[1;1R")
    assert responder.outstanding == 1
    admissions.completions[0](None)

    assert admissions.payloads == [b"\x1b[1;1R"]
    assert responder.reply_failed is False
    assert responder.outstanding == 0


def test_immediate_admission_pressure_records_the_kind_and_the_reason():
    admissions = _Admissions(immediate_reason="discarded before delivery (backpressure)")
    responder = TerminalQueryResponder(admissions)

    responder.answer(QUERY_DEVICE_STATUS, b"\x1b[0n")

    assert responder.reply_failed is True
    assert [(failure.kind, failure.reason) for failure in responder.failures] == [
        (QUERY_DEVICE_STATUS, "discarded before delivery (backpressure)")
    ]
    assert responder.outstanding == 0


def test_an_admission_that_raises_is_recorded_rather_than_propagated():
    """The reader feeds the parser; a reply must never be able to take it down."""
    responder = TerminalQueryResponder(_Admissions(raises=RuntimeError("no channel")))

    responder.answer(QUERY_DEVICE_ATTRIBUTES, b"\x1b[?6c")

    assert responder.reply_failed is True
    assert "admission raised" in responder.failures[0].reason
    assert responder.outstanding == 0


def test_a_reply_admitted_while_active_still_reports_after_quiescence():
    admissions = _Admissions()
    responder = TerminalQueryResponder(admissions)

    responder.answer(QUERY_CURSOR_POSITION, b"\x1b[1;1R")
    responder.quiesce()
    admissions.completions[0]("discarded before delivery (closed)")

    assert responder.reply_failed is True
    assert responder.failures[0].kind == QUERY_CURSOR_POSITION


def test_a_query_first_seen_after_quiescence_renders_and_records_nothing():
    admissions = _Admissions()
    responder = TerminalQueryResponder(admissions)

    responder.quiesce()
    responder.answer(QUERY_CURSOR_POSITION, b"\x1b[1;1R")

    assert admissions.payloads == []
    assert responder.render_only == 1
    assert responder.reply_failed is False


def test_quiescing_from_inside_an_admission_keeps_that_obligation():
    """The lock linearizes the two: a callback admits while active, or observes quiescence."""
    responder = TerminalQueryResponder()
    completions = []

    def admit(payload, on_complete):
        responder.quiesce()  # the transition cannot interleave with this callback
        completions.append(on_complete)

    responder._admit = admit
    responder._state = ResponderState.ACTIVE

    responder.answer(QUERY_CURSOR_POSITION, b"\x1b[2;3R")
    responder.answer(QUERY_DEVICE_STATUS, b"\x1b[0n")  # after the transition: render-only
    completions[0]("write failed: OSError(5)")

    assert responder.render_only == 1
    assert [failure.kind for failure in responder.failures] == [QUERY_CURSOR_POSITION]


def test_quiesce_is_idempotent():
    responder = TerminalQueryResponder(_Admissions())

    responder.quiesce()
    responder.quiesce()

    assert responder.state is ResponderState.QUIESCED


def test_a_completion_resolves_its_obligation_exactly_once():
    admissions = _Admissions()
    responder = TerminalQueryResponder(admissions)

    responder.answer(QUERY_CURSOR_POSITION, b"\x1b[1;1R")
    admissions.completions[0]("write failed: OSError(5)")
    admissions.completions[0]("discarded before delivery (closed)")

    assert len(responder.failures) == 1


def test_failure_detail_names_every_query_kind_and_reason():
    responder = TerminalQueryResponder(_Admissions(immediate_reason="discarded before delivery (backpressure)"))

    responder.answer(QUERY_CURSOR_POSITION, b"\x1b[1;1R")
    responder.answer(QUERY_DEVICE_STATUS, b"\x1b[0n")

    detail = responder.failure_detail()
    assert QUERY_CURSOR_POSITION in detail and QUERY_DEVICE_STATUS in detail
    assert detail.count("backpressure") == 2


def test_a_repeated_failure_is_counted_once_and_summarized():
    """A target that queries in a loop against a closed channel must not grow the history."""
    admissions = _Admissions(immediate_reason="discarded before delivery (closed)")
    responder = TerminalQueryResponder(admissions)

    for _ in range(5000):
        responder.answer(QUERY_CURSOR_POSITION, b"\x1b[1;1R")

    assert responder.failures_recorded == 5000
    assert len(responder.failures) == 1  # one kind, one reason
    detail = responder.failure_detail()
    assert "4999 further reply failures" in detail
    assert len(detail) < 200


def test_distinct_failure_reasons_are_sampled_rather_than_accumulated():
    """Reasons carry exception text, so distinctness cannot be an excuse to keep them all."""
    admissions = _Admissions()
    responder = TerminalQueryResponder(admissions)

    for _ in range(2000):
        responder.answer(QUERY_DEVICE_STATUS, b"\x1b[0n")
    for index, complete in enumerate(admissions.completions):
        complete(f"write failed: OSError({index})")

    assert responder.failures_recorded == 2000
    assert len(responder.failures) == MAX_TRACKED_FAILURES
    assert responder.outstanding == 0
    assert len(responder.failure_detail()) < 2000


def run_racing(first, second, reversed_order: bool) -> None:
    """Releases both threads together, from a third one, so neither is ahead by construction."""
    go = threading.Event()
    threads = [threading.Thread(target=lambda call=call: (go.wait(), call())) for call in (first, second)]
    if reversed_order:  # started in both orders, since the starter is itself a head start
        threads.reverse()
    for thread in threads:
        thread.start()
    go.set()
    for thread in threads:
        thread.join(SPAWN_TIMEOUT)
        assert not thread.is_alive()


def test_a_query_racing_quiescence_is_admitted_or_render_only_but_never_both():
    """Two threads, one lock: the callback either admits while active or observes the switch."""
    outcomes = {"admitted": 0, "render_only": 0}
    for attempt in range(200):
        admissions = _Admissions()
        responder = TerminalQueryResponder(admissions)

        run_racing(
            lambda: responder.answer(QUERY_CURSOR_POSITION, b"\x1b[1;1R"),
            responder.quiesce,
            reversed_order=bool(attempt % 2),
        )

        assert responder.state is ResponderState.QUIESCED
        assert responder.admitted + responder.render_only == 1
        assert responder.outstanding == responder.admitted
        for complete in admissions.completions:
            complete("discarded before delivery (closed)")
        assert responder.outstanding == 0
        assert responder.reply_failed is bool(responder.admitted)
        outcomes["admitted"] += responder.admitted
        outcomes["render_only"] += responder.render_only

    assert min(outcomes.values()) > 0, f"the race never went both ways: {outcomes}"


def test_a_completion_racing_teardown_resolves_its_obligation_exactly_once():
    """Teardown discards while the backend reports the write: one obligation, one record."""
    for attempt in range(200):
        admissions = _Admissions()
        responder = TerminalQueryResponder(admissions)
        responder.answer(QUERY_CURSOR_POSITION, b"\x1b[1;1R")
        complete = admissions.completions[0]

        def teardown():
            responder.quiesce()
            complete("discarded before delivery (closed)")

        run_racing(
            lambda: complete("write failed: OSError(5)"),
            teardown,
            reversed_order=bool(attempt % 2),
        )

        assert responder.failures_recorded == 1
        assert len(responder.failures) == 1
        assert responder.failures[0].kind == QUERY_CURSOR_POSITION
        assert responder.outstanding == 0


# --------------------------------------------------------------- backend integration


def write_target(tmp_path: Path, name: str, source: str) -> str:
    path = tmp_path / name
    path.write_text(source)
    return str(path)


# Switches to noncanonical, no-echo mode first, exactly as a real query emitter does: in
# canonical mode the newline-less reply never satisfies read(), and with echo on the reply
# bytes would land in the raw transcript.
READS_THE_REPLY = """
import os
import sys
import termios

fd = sys.stdin.fileno()
saved = termios.tcgetattr(fd)
raw = termios.tcgetattr(fd)
raw[3] &= ~(termios.ICANON | termios.ECHO)
raw[6][termios.VMIN] = 1
raw[6][termios.VTIME] = 0
termios.tcsetattr(fd, termios.TCSANOW, raw)
try:
    sys.stdout.write("\\x1b[6n")
    sys.stdout.flush()
    reply = b""
    while not reply.endswith(b"R"):
        chunk = os.read(fd, 1)
        if not chunk:
            sys.stdout.write("no reply\\n")
            sys.stdout.flush()
            raise SystemExit(3)
        reply += chunk
finally:
    termios.tcsetattr(fd, termios.TCSANOW, saved)

reply = reply[reply.index(b"\\x1b") :]  # the spawn-time EOF byte is still queued ahead of it
row, column = reply[2:-1].split(b";")
sys.stdout.write("answered row %s column %s\\n" % (row.decode(), column.decode()))
sys.stdout.flush()
"""

# Emits the query and carries on without waiting for it, which is what leaves the reply to
# fail on its own timeline.
ABANDONS_THE_REPLY = """
import sys

sys.stdout.write("\\x1b[6n")
sys.stdout.flush()
sys.stdout.write("carried on\\n")
sys.stdout.flush()
"""


def run_target(script, **spawn_kwargs):
    """Spawns a target, drains it to exit, and always tears it down."""
    process = _posix_pty.PosixPtyProcess()
    process.spawn([sys.executable, script], **spawn_kwargs)
    return process


def drain_to_exit(process, timeout=SPAWN_TIMEOUT):
    deadline = time.monotonic() + timeout
    raw = bytearray()
    while time.monotonic() < deadline:
        raw += process.read_raw_output()
        returncode = process.poll()
        if returncode is not None:
            raw += process.read_raw_output()
            return returncode, bytes(raw)
        time.sleep(0.01)
    raise AssertionError(f"the target did not exit within {timeout}s; output so far {bytes(raw)!r}")


@posix_only
def test_a_live_cursor_position_query_is_answered_and_the_target_completes(tmp_path):
    """The reply reaches a target that is blocked reading it, so it completes, not times out."""
    script = write_target(tmp_path, "reads_the_reply.py", READS_THE_REPLY)
    process = run_target(script)
    caller_writes = []
    original_write_input = process.write_input
    process.write_input = lambda data: caller_writes.append(data) or original_write_input(data)
    try:
        returncode, raw = drain_to_exit(process)
        normalized = process.normalized_output()
    finally:
        process.terminate_tree(grace=0.05)
        process.close()

    assert returncode == 0
    assert "answered row 1 column 1" in normalized
    assert process.query_responder.admitted == 1
    assert process.terminal_reply_failed is False
    # The reply is terminal protocol: it is not caller input and it is in neither transcript.
    assert caller_writes == []
    assert b"\x1b[1;1R" not in raw
    assert "\x1b" not in normalized


@posix_only
def test_immediate_reply_pressure_is_recorded_without_stalling_the_reader(tmp_path):
    script = write_target(tmp_path, "abandons_the_reply.py", ABANDONS_THE_REPLY)
    process = _posix_pty.PosixPtyProcess()
    original_submit = process._input_queue.submit

    def rejecting_submit(data, reserved=False, prepare=None, finish=None, on_resolve=None):
        if not data.startswith(b"\x1b"):  # the spawn-time EOF still goes through
            return original_submit(data, reserved=reserved, prepare=prepare, finish=finish, on_resolve=on_resolve)
        receipt = _posix_pty._Receipt(on_resolve)
        receipt.resolve(InputDisposition.BACKPRESSURE)
        return InputWriteResult(InputDisposition.BACKPRESSURE, 0), receipt

    process._input_queue.submit = rejecting_submit
    try:
        process.spawn([sys.executable, script])
        returncode, _ = drain_to_exit(process)
        normalized = process.normalized_output()
    finally:
        process.terminate_tree(grace=0.05)
        process.close()

    assert returncode == 0
    assert "carried on" in normalized, "the reader kept draining after the reply was refused"
    assert process.terminal_reply_failed is True
    assert process.query_responder.failures[0].kind == QUERY_CURSOR_POSITION
    assert "backpressure" in process.terminal_reply_detail()
    assert process.reader_failed.is_set() is False


@posix_only
def test_a_reply_discarded_at_teardown_still_records_a_failure(tmp_path):
    """Admitted while ACTIVE, so the obligation survives the transition teardown makes."""
    script = write_target(tmp_path, "abandons_the_reply.py", ABANDONS_THE_REPLY)
    process = _posix_pty.PosixPtyProcess()
    original_flush = process._flush_input

    def stall_replies(master_fd, budget):
        item = process._input_queue.current()
        if item is not None and item.data.startswith(b"\x1b"):
            return  # a reply never reaches the fd; the spawn-time EOF still does
        original_flush(master_fd, budget)

    # Installed before the spawn, so no reply can complete before the stall is in place.
    process._flush_input = stall_replies
    try:
        process.spawn([sys.executable, script])
        drain_to_exit(process)
        assert process.query_responder.admitted == 1
    finally:
        process.terminate_tree(grace=0.05)
        process.close()

    assert process.query_responder.state is ResponderState.QUIESCED
    assert process.terminal_reply_failed is True
    assert process.query_responder.failures[0].kind == QUERY_CURSOR_POSITION
    assert "discarded before delivery" in process.terminal_reply_detail()


@posix_only
def test_a_reply_that_fails_its_native_write_is_recorded_separately_from_the_reader(tmp_path):
    script = write_target(tmp_path, "abandons_the_reply.py", ABANDONS_THE_REPLY)
    process = _posix_pty.PosixPtyProcess()
    original_write = process._write_master

    def failing_write(fd, data):
        if data.startswith(b"\x1b"):
            raise OSError(5, "injected write failure")
        return original_write(fd, data)

    process._write_master = failing_write
    try:
        process.spawn([sys.executable, script])
        deadline = time.monotonic() + SPAWN_TIMEOUT
        while time.monotonic() < deadline and not process.terminal_reply_failed:
            time.sleep(0.01)
    finally:
        process.terminate_tree(grace=0.05)
        process.close()

    assert process.terminal_reply_failed is True
    failure = process.query_responder.failures[0]
    assert failure.kind == QUERY_CURSOR_POSITION
    assert "write failed" in failure.reason
    assert process.reader_failed.is_set() is True  # an independent signal, not the same one


@posix_only
def test_a_query_seen_only_after_quiescence_renders_and_records_nothing(tmp_path):
    script = write_target(tmp_path, "abandons_the_reply.py", ABANDONS_THE_REPLY)
    process = run_target(script)
    try:
        drain_to_exit(process)  # poll() observed the outcome, so the responder is quiesced
        assert process.query_responder.state is ResponderState.QUIESCED
        admitted_before = process.query_responder.admitted

        process.normalizer.feed(b"\x1b[6ntrailing frame\r\n")  # the reader's byte-feed hook

        assert process.query_responder.admitted == admitted_before
        assert process.query_responder.render_only == 1
        assert process.terminal_reply_failed is False
        assert "trailing frame" in process.normalized_output()
    finally:
        process.terminate_tree(grace=0.05)
        process.close()


# ------------------------------------------------------------- reply resolution
#
# One queue resolution, mapped onto the responder's delivered / not-delivered contract.
# Both backends admit their replies through it.


def test_a_delivered_reply_reports_no_reason():
    reasons = []
    reply_resolution(reasons.append)(InputDisposition.ACCEPTED, None)

    assert reasons == [None]


def test_a_failed_reply_reports_the_write_failure():
    reasons = []
    reply_resolution(reasons.append)(InputDisposition.ACCEPTED, OSError("gone"))

    assert "write failed" in reasons[0]


def test_a_discarded_reply_reports_the_disposition():
    reasons = []
    reply_resolution(reasons.append)(InputDisposition.CLOSED, None)

    assert "discarded" in reasons[0] and "closed" in reasons[0]
