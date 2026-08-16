"""The platform-neutral half of the Windows ConPTY backend.

Everything here is ordinary Python and runs on every platform, which is the point: the
marshaling rules and the writer protocol are the parts of the backend whose failures are
silent — a truncated command line, a retried cancelled write, a writer parked on a gate
nobody released — and they would otherwise be provable only on a Windows runner.
"""

import subprocess
import threading
import time

import pytest

from plain2code_exceptions import RenderCancelledError
from render_machine import _conpty_support as support
from render_machine._conpty_support import (
    CANCEL_TICK_SECONDS,
    GateDecision,
    InputLane,
    InputQueue,
    InputWriter,
    Receipt,
    WriteAborted,
    WriteChannel,
    build_command_line,
    build_environment_block,
    validate_working_directory,
)
from render_machine.terminal_process import InputDisposition, TerminalEnvironmentError

# Every wait below is bounded, so a failure is a failure rather than a hung suite.
SHORT_TIMEOUT = 5.0
NUL = "\x00"


class FakeChannel(WriteChannel):
    """The two native operations, recorded.

    `park` makes a write block until it is cancelled, which is the state a target that has
    stopped reading its input leaves the writer in.
    """

    def __init__(self, chunk=None):
        self.writes = []
        self.written = bytearray()
        self.cancels = 0
        self.chunk = chunk
        self.park = False
        self.prefix_before_abort = 0
        self.fail = None
        self.entered = threading.Event()
        self._release = threading.Event()

    def write(self, data: bytes) -> int:
        self.writes.append(bytes(data))
        if self.fail is not None:
            raise self.fail
        if self.park:
            self.entered.set()
            if not self._release.wait(SHORT_TIMEOUT):
                raise AssertionError("the parked write was never cancelled")
            self._release.clear()
            self.entered.clear()
            # A cancelled synchronous write may already have moved a prefix; the completion
            # carries no trustworthy cursor either way.
            self.written += data[: self.prefix_before_abort]
            raise WriteAborted("cancelled")
        count = len(data) if self.chunk is None else min(self.chunk, len(data))
        self.written += data[:count]
        return count

    def cancel(self) -> None:
        self.cancels += 1
        self._release.set()


class LateCancelChannel(FakeChannel):
    """Ignores its first cancels, the way `CancelSynchronousIo` reports ERROR_NOT_FOUND when
    the writer has not entered its write yet."""

    def __init__(self, ignore_first=1):
        super().__init__()
        self.ignored = ignore_first

    def cancel(self) -> None:
        self.cancels += 1
        if self.ignored > 0:
            self.ignored -= 1
            return  # nothing was in flight, so the call reached nothing
        self._release.set()


class SlowSubmitQueue(InputQueue):
    """Widens the window between an item becoming visible and whatever the poster does next.

    With the generation published under the same lock as the enqueue, a writer that dequeues
    inside this window blocks on that lock before it can acknowledge anything. Published
    afterwards, it acknowledges a generation that does not exist yet.
    """

    def submit(self, *args, **kwargs):
        result = super().submit(*args, **kwargs)
        time.sleep(CANCEL_TICK_SECONDS * 5)
        return result


class ControlParkChannel(WriteChannel):
    """Parks inside the control write and never releases itself on cancel.

    That is what makes a cancel aimed at the control write observable: the test, not the
    cancellation, decides when the write completes.
    """

    def __init__(self):
        self.entered = threading.Event()
        self.release = threading.Event()
        self.cancels = 0
        self.written = bytearray()

    def write(self, data: bytes) -> int:
        if data[:1] == b"\x03":
            self.entered.set()
            if not self.release.wait(SHORT_TIMEOUT):
                raise AssertionError("the control write was never released")
        self.written += data
        return len(data)

    def cancel(self) -> None:
        self.cancels += 1


def wait_until(predicate, timeout=SHORT_TIMEOUT):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


# ------------------------------------------------------------------- marshaling


@pytest.mark.parametrize(
    "argv",
    [
        ["script.ps1", "one two"],
        ["script.ps1", 'say "hello"'],
        ["script.ps1", "trailing\\\\"],
        ["script.ps1", ""],
        ["script.ps1", "C:\\path with space\\", "plain"],
    ],
)
def test_the_command_line_is_the_quoting_subprocess_already_produces(argv):
    """The naive cases: spaces, embedded quotes, trailing backslashes and the empty string
    are where hand-rolled quoting produces a different argv without erroring."""
    assert build_command_line(argv) == subprocess.list2cmdline(argv)


def test_the_empty_string_argument_is_quoted_rather_than_dropped():
    assert build_command_line(["script.ps1", ""]).endswith('""')


def test_an_empty_command_is_refused():
    with pytest.raises(TerminalEnvironmentError):
        build_command_line([])


def test_a_nul_in_an_argument_is_refused_before_anything_is_built():
    with pytest.raises(TerminalEnvironmentError) as error:
        build_command_line(["script.ps1", f"before{NUL}after"])
    assert "NUL" in str(error.value)


def test_a_nul_in_the_working_directory_is_refused():
    with pytest.raises(TerminalEnvironmentError):
        validate_working_directory(f"C:\\builds{NUL}")


def test_a_working_directory_passes_through_unchanged():
    assert validate_working_directory("C:\\builds") == "C:\\builds"
    assert validate_working_directory(None) is None


def test_a_nul_in_an_environment_name_is_refused():
    with pytest.raises(TerminalEnvironmentError):
        build_environment_block({f"NA{NUL}ME": "value"})


def test_a_nul_in_an_environment_value_is_refused():
    with pytest.raises(TerminalEnvironmentError):
        build_environment_block({"NAME": f"va{NUL}lue"})


def test_an_environment_name_carrying_the_block_separator_is_refused():
    with pytest.raises(TerminalEnvironmentError) as error:
        build_environment_block({"NA=ME": "value"})
    assert "'='" in str(error.value)


def test_an_empty_environment_name_is_refused():
    with pytest.raises(TerminalEnvironmentError):
        build_environment_block({"": "value"})


def test_the_environment_block_is_sorted_case_insensitively():
    block = build_environment_block({"beta": "2", "Alpha": "1", "GAMMA": "3"})

    assert block == f"Alpha=1{NUL}beta=2{NUL}GAMMA=3{NUL}"


def test_an_empty_environment_block_still_terminates():
    """The buffer's own terminator supplies the second NUL, so one is enough here."""
    assert build_environment_block({}) == NUL


# ------------------------------------------------------------------- the queue


def test_admission_accounts_for_the_item_until_it_is_retired():
    queue = InputQueue()

    result, receipt = queue.submit(b"abcd")

    assert result.disposition is InputDisposition.ACCEPTED
    assert result.accepted_bytes == 4
    assert queue.pending_bytes() == 4
    queue.next_item(0)  # dequeue is not completion
    assert queue.pending_bytes() == 4
    queue.retire_current(delivered=True)
    assert queue.pending_bytes() == 0
    assert receipt.delivered


def test_an_empty_item_never_becomes_an_entry():
    queue = InputQueue()

    result, receipt = queue.submit(b"")

    assert result.disposition is InputDisposition.ACCEPTED
    assert queue.pending_items() == 0
    assert receipt.resolved


def test_an_oversized_item_is_refused_whole():
    queue = InputQueue(max_item_bytes=4)

    result, _ = queue.submit(b"abcde")

    assert result.disposition is InputDisposition.BACKPRESSURE
    assert result.accepted_bytes == 0


def test_a_data_backlog_cannot_crowd_out_the_reserved_partition():
    queue = InputQueue(max_pending_bytes=10, reserved_bytes=4, max_pending_items=10, reserved_items=4)

    assert queue.submit(b"123456")[0].disposition is InputDisposition.ACCEPTED
    assert queue.submit(b"7")[0].disposition is InputDisposition.BACKPRESSURE
    assert queue.submit(b"7", reserved=True)[0].disposition is InputDisposition.ACCEPTED


def test_control_items_are_serviced_ahead_of_queued_data():
    queue = InputQueue()
    queue.submit(b"data")
    queue.submit(b"\x03", reserved=True, lane=InputLane.CONTROL)

    assert queue.next_item(0).data == b"\x03"


def test_a_requeued_item_keeps_its_place_and_its_accounting():
    queue = InputQueue()
    queue.submit(b"abc")
    queue.next_item(0)

    queue.requeue_current_front()

    assert queue.pending_bytes() == 3
    assert queue.next_item(0).data == b"abc"


def test_discarding_data_leaves_the_item_under_the_cursor_alone():
    queue = InputQueue()
    queue.submit(b"first")
    queue.submit(b"second")
    in_flight = queue.next_item(0)

    discarded = queue.discard_pending_data()

    assert [item.data for item in discarded] == [b"second"]
    assert queue.current() is in_flight
    assert discarded[0].receipt.resolved and not discarded[0].receipt.delivered


def test_closing_the_queue_resolves_every_receipt_once():
    queue = InputQueue()
    _, first = queue.submit(b"one")
    _, second = queue.submit(b"two")
    queue.next_item(0)

    queue.close_and_fail_all()

    assert first.resolutions == 1 and second.resolutions == 1
    assert first.attempts == 1 and second.attempts == 1
    assert not first.delivered and not second.delivered
    assert queue.submit(b"three")[0].disposition is InputDisposition.CLOSED


def test_a_receipt_reports_its_resolution_to_the_producer():
    seen = []
    receipt = Receipt(lambda disposition, error: seen.append((disposition, error)))

    receipt.resolve(InputDisposition.ACCEPTED)
    receipt.resolve(InputDisposition.CLOSED)  # a second resolution changes nothing

    assert seen == [(InputDisposition.ACCEPTED, None)]
    assert receipt.disposition is InputDisposition.ACCEPTED
    assert receipt.resolutions == 1  # what took effect
    assert receipt.attempts == 2  # what was tried, so a double retirement is still visible


# ------------------------------------------------------------------ the writer


def start_writer(queue, channel, decision=None):
    """Runs the creator's half of the gate protocol and returns the started writer."""
    writer = InputWriter(queue, channel)
    writer.start()
    native_id = writer.await_ready(time.monotonic() + SHORT_TIMEOUT)
    writer.gate.set(GateDecision.RUN if decision is None else decision)
    return writer, native_id


def test_the_writer_publishes_its_native_id_before_it_parks():
    queue, channel = InputQueue(), FakeChannel()

    writer, native_id = start_writer(queue, channel)
    try:
        assert native_id is not None and native_id == writer.native_id
    finally:
        writer.stop(SHORT_TIMEOUT)


def test_a_writer_released_with_abort_never_touches_the_pipe():
    queue, channel = InputQueue(), FakeChannel()
    queue.submit(b"payload")

    writer, _ = start_writer(queue, channel, decision=GateDecision.ABORT)

    assert wait_until(writer.finished.is_set)
    assert channel.writes == []
    assert not writer.failed.is_set()


def test_a_writer_that_dies_before_publishing_its_id_still_releases_the_creator(monkeypatch):
    def unavailable():
        raise OSError("no native id")

    monkeypatch.setattr(support, "native_thread_id", unavailable)
    queue, channel = InputQueue(), FakeChannel()

    writer, native_id = start_writer(queue, channel)

    assert native_id is None
    assert wait_until(writer.failed.is_set)
    assert channel.writes == []


def test_the_ready_wait_gives_up_at_its_deadline():
    """A writer that never starts must not park the creator forever."""
    writer = InputWriter(InputQueue(), FakeChannel())  # deliberately not started

    assert writer.await_ready(time.monotonic() + 0.05) is None


def test_a_whole_item_is_written_and_its_receipt_reports_delivery():
    queue, channel = InputQueue(), FakeChannel(chunk=2)
    writer, _ = start_writer(queue, channel)
    try:
        _, receipt = queue.submit(b"abcdef")

        assert wait_until(lambda: receipt.resolved)
        assert receipt.delivered
        assert bytes(channel.written) == b"abcdef"
    finally:
        writer.stop(SHORT_TIMEOUT)


def test_an_urgent_control_item_cancels_the_data_write_in_flight():
    queue, channel = InputQueue(), FakeChannel()
    channel.park = True
    writer, _ = start_writer(queue, channel)
    try:
        _, data_receipt = queue.submit(b"blocked payload")
        assert channel.entered.wait(SHORT_TIMEOUT)
        channel.park = False  # the control write itself completes

        assert writer.deliver_control(b"\x03", SHORT_TIMEOUT)

        assert bytes(channel.written).endswith(b"\x03")
        assert data_receipt.resolved and not data_receipt.delivered
        assert channel.cancels >= 1
    finally:
        writer.stop(SHORT_TIMEOUT)


def test_a_cancelled_write_is_never_retried_and_never_duplicates_its_prefix():
    queue, channel = InputQueue(), FakeChannel()
    channel.park = True
    channel.prefix_before_abort = 3
    writer, _ = start_writer(queue, channel)
    try:
        _, data_receipt = queue.submit(b"abcdef")
        assert channel.entered.wait(SHORT_TIMEOUT)
        channel.park = False

        assert writer.deliver_control(b"\x03", SHORT_TIMEOUT)

        assert channel.writes.count(b"abcdef") == 1  # the buffer is never reissued
        assert bytes(channel.written) == b"abc\x03"
        assert data_receipt.resolutions == 1
        assert data_receipt.attempts == 1  # retired once, never resolved a second time
    finally:
        writer.stop(SHORT_TIMEOUT)


def test_no_cancel_is_issued_once_the_writer_has_acknowledged_the_generation():
    queue, channel = InputQueue(), FakeChannel()
    writer, _ = start_writer(queue, channel)
    try:
        assert writer.deliver_control(b"\x03", SHORT_TIMEOUT)
        settled = writer.cancels

        time.sleep(CANCEL_TICK_SECONDS * 5)

        assert writer.cancels == settled
    finally:
        writer.stop(SHORT_TIMEOUT)


def test_an_undelivered_control_item_reports_failure_rather_than_waiting_out_the_grace():
    queue = InputQueue(max_pending_bytes=0, reserved_bytes=0)  # no capacity for anything
    writer, _ = start_writer(queue, FakeChannel())
    try:
        assert writer.deliver_control(b"\x03", 0.2) is False
    finally:
        writer.stop(SHORT_TIMEOUT)


def test_stopping_an_idle_writer_joins_it():
    """An idle writer is parked on the queue rather than inside a write, so a cancel-only
    loop would never join it."""
    queue, channel = InputQueue(), FakeChannel()
    writer, _ = start_writer(queue, channel)

    assert writer.stop(SHORT_TIMEOUT)
    assert channel.writes == []


def test_stopping_discards_queued_data_rather_than_writing_it():
    queue, channel = InputQueue(), FakeChannel()
    channel.park = True
    writer, _ = start_writer(queue, channel)
    _, first = queue.submit(b"in flight")
    assert channel.entered.wait(SHORT_TIMEOUT)
    _, second = queue.submit(b"queued behind it")

    assert writer.stop(SHORT_TIMEOUT)

    assert first.resolved and not first.delivered
    assert second.resolved and not second.delivered
    assert b"queued behind it" not in channel.writes


def test_a_write_cancelled_by_the_stop_protocol_is_not_a_writer_failure():
    queue, channel = InputQueue(), FakeChannel()
    channel.park = True
    writer, _ = start_writer(queue, channel)
    queue.submit(b"blocked payload")
    assert channel.entered.wait(SHORT_TIMEOUT)

    assert writer.stop(SHORT_TIMEOUT)

    assert not writer.failed.is_set()


def test_a_cancellation_nobody_asked_for_is_a_writer_failure():
    queue, channel = InputQueue(), FakeChannel()
    channel.fail = WriteAborted("cancelled by nobody")
    writer, _ = start_writer(queue, channel)
    try:
        queue.submit(b"payload")

        assert wait_until(writer.failed.is_set)
        assert isinstance(writer.exc, WriteAborted)
    finally:
        writer.stop(SHORT_TIMEOUT)


def test_a_write_failure_is_published_to_the_foreground():
    queue, channel = InputQueue(), FakeChannel()
    channel.fail = OSError("the pipe is gone")
    writer, _ = start_writer(queue, channel)
    try:
        _, receipt = queue.submit(b"payload")

        assert wait_until(writer.failed.is_set)
        assert isinstance(writer.exc, OSError)
        assert receipt.resolved and not receipt.delivered
    finally:
        writer.stop(SHORT_TIMEOUT)


def test_stopping_retries_the_cancel_that_reached_nothing():
    """A cancel issued in the dequeue-to-write gap reports ERROR_NOT_FOUND and the writer
    then blocks after it, so a one-shot cancel would hang to the bound."""
    queue, channel = InputQueue(), LateCancelChannel(ignore_first=1)
    channel.park = True
    writer, _ = start_writer(queue, channel)
    queue.submit(b"blocked payload")
    assert channel.entered.wait(SHORT_TIMEOUT)

    assert writer.stop(SHORT_TIMEOUT)

    assert channel.cancels >= 2  # the first reached nothing; a later tick landed
    assert channel.ignored == 0


def test_a_control_item_is_delivered_even_when_the_first_cancel_reaches_nothing():
    queue, channel = InputQueue(), LateCancelChannel(ignore_first=1)
    channel.park = True
    writer, _ = start_writer(queue, channel)
    try:
        queue.submit(b"blocked payload")
        assert channel.entered.wait(SHORT_TIMEOUT)
        channel.park = False

        assert writer.deliver_control(b"\x03", SHORT_TIMEOUT)

        assert channel.cancels >= 2
        assert bytes(channel.written).endswith(b"\x03")
    finally:
        writer.stop(SHORT_TIMEOUT)


def test_an_idle_writers_control_write_is_never_cancelled():
    """The generation is published under the same lock that makes the item visible, so a
    writer that dequeues it immediately has already acknowledged the request the poster is
    about to wait on — otherwise the poster cancels the very write it asked for."""
    channel = ControlParkChannel()
    writer, _ = start_writer(SlowSubmitQueue(), channel)
    delivered = []
    poster = threading.Thread(target=lambda: delivered.append(writer.deliver_control(b"\x03", SHORT_TIMEOUT)))
    try:
        poster.start()
        assert channel.entered.wait(SHORT_TIMEOUT)
        cancels_at_entry = channel.cancels

        time.sleep(CANCEL_TICK_SECONDS * 5)

        assert channel.cancels == cancels_at_entry
        channel.release.set()
        poster.join(SHORT_TIMEOUT)
        assert delivered == [True]
    finally:
        channel.release.set()
        poster.join(SHORT_TIMEOUT)
        writer.stop(SHORT_TIMEOUT)


def test_a_full_data_queue_still_admits_the_graceful_control_byte():
    """Query replies are ordinary admissions, so a query-emitting target cannot fill the
    capacity cancellation depends on."""
    queue = InputQueue(max_pending_bytes=64, reserved_bytes=16, max_pending_items=8, reserved_items=2)
    channel = FakeChannel()
    channel.park = True
    writer, _ = start_writer(queue, channel)
    try:
        replies = [queue.submit(b"reply")[0] for _ in range(16)]
        assert channel.entered.wait(SHORT_TIMEOUT)  # the writer is genuinely blocked in a write
        assert any(result.disposition is InputDisposition.BACKPRESSURE for result in replies)
        channel.park = False

        assert writer.deliver_control(b"\x03", SHORT_TIMEOUT)

        # First on the wire: the control lane is serviced ahead of everything queued behind
        # the write it preempted.
        assert bytes(channel.written).startswith(b"\x03")
    finally:
        writer.stop(SHORT_TIMEOUT)


def test_a_saturated_writer_still_stops_within_the_bound():
    queue, channel = InputQueue(), FakeChannel()
    channel.park = True
    writer, _ = start_writer(queue, channel)
    for _ in range(50):
        queue.submit(b"reply")
    assert channel.entered.wait(SHORT_TIMEOUT)

    started = time.monotonic()
    assert writer.stop(SHORT_TIMEOUT)

    assert time.monotonic() - started < SHORT_TIMEOUT


def test_the_ready_wait_reports_a_cancellation_that_arrives_after_readiness():
    """The stop check runs at least once even when the writer is already ready: a render
    cancelled during writer startup must not proceed to launch the target."""
    writer = InputWriter(InputQueue(), FakeChannel())
    writer.start()
    assert wait_until(writer.ready.is_set)

    def cancelled():
        raise RenderCancelledError()

    try:
        with pytest.raises(RenderCancelledError):
            writer.await_ready(time.monotonic() + SHORT_TIMEOUT, cancelled)
    finally:
        writer.stop(SHORT_TIMEOUT)
