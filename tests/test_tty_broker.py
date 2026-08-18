"""Tests for the per-execution `codeplain-tty` broker and its helper CLI.

Two layers. The protocol and broker cases talk to the broker directly over its socket
with a fake terminal process, so authentication, bounds, and every error channel are
asserted without a real target. The end-to-end cases spawn real interactive programs on
the POSIX PTY backend and drive them through the actual helper executable the broker
installs — including the `getpass` reproduction whose `TCSAFLUSH` defeats the spawn-time
VEOF, the exact mechanism that motivated the broker.
"""

import os
import socket
import stat
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest

from render_machine import tty_protocol
from render_machine.terminal_process import InputDisposition, InputWriteResult, TerminalInputDriver
from render_machine.tty_broker import TtyBroker, broker_supported

posix_only = pytest.mark.skipif(
    sys.platform == "win32",
    reason="The broker transport and these interactive targets are POSIX-only.",
)

pytestmark = posix_only

if sys.platform != "win32":
    from render_machine._posix_pty import PosixPtyProcess

SPAWN_TIMEOUT = 20.0


class FakeProcess:
    """A terminal process double: a settable transcript and a recording input sink."""

    def __init__(self) -> None:
        self.transcript = ""
        self.written = b""
        self.resized_to = None
        self.dispositions = [InputDisposition.ACCEPTED]

    def normalized_output(self) -> str:
        return self.transcript

    def write_input(self, data: bytes) -> InputWriteResult:
        disposition = self.dispositions[0] if len(self.dispositions) == 1 else self.dispositions.pop(0)
        if disposition is InputDisposition.ACCEPTED:
            self.written += data
            return InputWriteResult(disposition, len(data))
        return InputWriteResult(disposition, 0)

    def resize(self, columns: int, rows: int) -> None:
        self.resized_to = (columns, rows)


@pytest.fixture
def broker():
    process = FakeProcess()
    instance = TtyBroker(process)
    instance.start()
    try:
        yield instance, process
    finally:
        instance.close()


def call(instance: TtyBroker, payload: dict) -> dict:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(10.0)
        connection.connect(instance.endpoint)
        connection.sendall(tty_protocol.encode_frame(payload))
        response = tty_protocol.read_frame(connection.recv)
    assert response is not None
    return response


def command(instance: TtyBroker, name: str, args: dict) -> dict:
    return call(instance, tty_protocol.request(instance._token, name, args))


def test_the_transport_is_supported_on_this_platform():
    assert broker_supported()


def test_the_endpoint_lives_in_a_private_directory_with_the_helper(broker):
    instance, _ = broker
    endpoint_dir = os.path.dirname(instance.endpoint)
    assert stat.S_IMODE(os.stat(endpoint_dir).st_mode) == 0o700
    helper = os.path.join(instance.helper_bin_dir, "codeplain-tty")
    assert os.access(helper, os.X_OK)
    env = instance.child_env()
    assert env[tty_protocol.ENDPOINT_ENV_VAR] == instance.endpoint
    assert env[tty_protocol.TOKEN_ENV_VAR]


def test_a_wrong_token_is_rejected_in_every_command(broker):
    instance, process = broker
    process.transcript = "ready"
    response = call(instance, tty_protocol.request("not-the-token", "wait-for", {"text": "ready"}))
    assert response["ok"] is False
    assert response["error"] == tty_protocol.ERROR_UNAUTHORIZED


def test_a_future_protocol_version_is_rejected(broker):
    instance, _ = broker
    payload = tty_protocol.request(instance._token, "send-text", {"text": "x"})
    payload["protocol_version"] = 2
    response = call(instance, payload)
    assert response["error"] == tty_protocol.ERROR_UNSUPPORTED


def test_wait_for_resolves_once_the_text_appears(broker):
    instance, process = broker

    def appear_later():
        time.sleep(0.2)
        process.transcript = "Master password:"

    threading.Thread(target=appear_later, daemon=True).start()
    response = command(instance, "wait-for", {"text": "password:", "timeout": 5})
    assert response["ok"] is True


def test_wait_for_matches_a_prompt_despite_trailing_whitespace(broker):
    """The transcript is rendered per line with trailing whitespace stripped, so a
    needle quoting a prompt verbatim — 'Master password: ' — could never match as
    written. The broker strips end-of-line whitespace from the needle to compensate."""
    instance, process = broker
    process.transcript = "Master password:"
    response = command(instance, "wait-for", {"text": "Master password: ", "timeout": 2})
    assert response["ok"] is True


def test_wait_until_absent_applies_the_same_needle_normalization(broker):
    instance, process = broker
    process.transcript = "spinner"

    def clear_later():
        time.sleep(0.2)
        process.transcript = "done"

    threading.Thread(target=clear_later, daemon=True).start()
    response = command(instance, "wait-until-absent", {"text": "spinner ", "timeout": 5})
    assert response["ok"] is True


def test_a_whitespace_only_wait_needle_is_a_usage_error(broker):
    instance, _ = broker
    response = command(instance, "wait-for", {"text": "   ", "timeout": 2})
    assert response["error"] == tty_protocol.ERROR_INVALID_REQUEST


def test_wait_for_consumes_the_transcript_through_its_match(broker):
    """Expect-style sequencing: a successful wait-for advances a cursor, so the next
    wait-for matches only output produced after it. Without this, the second of two
    sequential interactive children matches the first child's stale prompt instantly,
    types into a terminal nobody is reading yet, and hangs the whole script."""
    instance, process = broker
    process.transcript = "Master password:"
    assert command(instance, "wait-for", {"text": "Master password:", "timeout": 2})["ok"] is True

    # The same text again, with no new output: must NOT match the stale occurrence.
    response = command(instance, "wait-for", {"text": "Master password:", "timeout": 0.3})
    assert response["error"] == tty_protocol.ERROR_TIMEOUT

    # A second occurrence beyond the cursor matches.
    process.transcript = "Master password:\nVault initialized\nMaster password:"
    assert command(instance, "wait-for", {"text": "Master password:", "timeout": 2})["ok"] is True


def test_wait_until_absent_looks_only_beyond_the_cursor(broker):
    instance, process = broker
    process.transcript = "spinner"
    assert command(instance, "wait-for", {"text": "spinner", "timeout": 2})["ok"] is True
    # The consumed occurrence no longer counts as present.
    assert command(instance, "wait-until-absent", {"text": "spinner", "timeout": 2})["ok"] is True


def test_wait_for_times_out_with_the_timeout_error(broker):
    instance, _ = broker
    response = command(instance, "wait-for", {"text": "never", "timeout": 0.2})
    assert response["error"] == tty_protocol.ERROR_TIMEOUT


def test_wait_until_absent_resolves_when_the_text_leaves(broker):
    instance, process = broker
    process.transcript = "spinner"

    def clear_later():
        time.sleep(0.2)
        process.transcript = "done"

    threading.Thread(target=clear_later, daemon=True).start()
    response = command(instance, "wait-until-absent", {"text": "spinner", "timeout": 5})
    assert response["ok"] is True


def test_send_text_types_newlines_as_carriage_returns(broker):
    instance, process = broker
    response = command(instance, "send-text", {"text": "hunter2\n"})
    assert response["ok"] is True
    assert process.written == b"hunter2\r"


def test_send_control_sends_the_control_byte(broker):
    instance, process = broker
    assert command(instance, "send-control", {"key": "d"})["ok"] is True
    assert process.written == b"\x04"


def test_send_hex_sends_exact_bytes(broker):
    instance, process = broker
    assert command(instance, "send-hex", {"hex": "1b5b41"})["ok"] is True
    assert process.written == b"\x1b[A"


def test_invalid_hex_is_a_usage_error_not_a_broker_failure(broker):
    instance, _ = broker
    response = command(instance, "send-hex", {"hex": "zz"})
    assert response["error"] == tty_protocol.ERROR_INVALID_REQUEST


def test_closed_input_is_reported_as_input_closed(broker):
    instance, process = broker
    process.dispositions = [InputDisposition.CLOSED]
    response = command(instance, "send-text", {"text": "x"})
    assert response["error"] == tty_protocol.ERROR_INPUT_CLOSED


def test_backpressure_is_retried_until_accepted(broker):
    instance, process = broker
    process.dispositions = [InputDisposition.BACKPRESSURE, InputDisposition.BACKPRESSURE, InputDisposition.ACCEPTED]
    response = command(instance, "send-text", {"text": "x"})
    assert response["ok"] is True
    assert process.written == b"x"


def test_size_resizes_the_process(broker):
    instance, process = broker
    assert command(instance, "size", {"columns": 100, "rows": 30})["ok"] is True
    assert process.resized_to == (100, 30)


def test_an_unknown_command_is_rejected(broker):
    instance, _ = broker
    response = command(instance, "reboot", {})
    assert response["error"] == tty_protocol.ERROR_INVALID_REQUEST


def test_an_oversized_frame_is_rejected_by_the_protocol(broker):
    instance, _ = broker
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(10.0)
        connection.connect(instance.endpoint)
        # A header claiming more than the bound; the broker must refuse without reading it.
        connection.sendall((tty_protocol.MAX_FRAME_BYTES + 1).to_bytes(4, "big"))
        response = tty_protocol.read_frame(connection.recv)
    assert response is not None
    assert response["error"] == tty_protocol.ERROR_INVALID_REQUEST


def test_close_removes_every_artifact_and_stops_the_server():
    process = FakeProcess()
    instance = TtyBroker(process)
    instance.start()
    directory = os.path.dirname(instance.endpoint)
    instance.close()
    assert not os.path.exists(directory)
    instance.close()  # idempotent


def test_the_broker_is_a_typed_input_driver(broker):
    instance, _ = broker
    assert isinstance(instance, TerminalInputDriver)
    assert "codeplain-tty" in instance.description()


# ------------------------------------------------------------------ end to end


def make_script(directory: Path, name: str, program: str) -> str:
    script_path = directory / f"{name}.py"
    script_path.write_text(f"#!{sys.executable}\n" + textwrap.dedent(program))
    script_path.chmod(script_path.stat().st_mode | stat.S_IXUSR)
    return str(script_path)


def run_with_broker(tmp_path: Path, name: str, program: str, driver_script: str) -> tuple:
    """Spawns `program` on the real PTY backend and runs `driver_script` (a shell script
    using codeplain-tty) against it from the outside, the way a generated test would."""
    target = make_script(tmp_path, name, program)
    process = PosixPtyProcess()
    broker = TtyBroker(process)
    broker.start()
    try:
        env = dict(os.environ)
        env.update(broker.child_env())
        env["PATH"] = broker.helper_bin_dir + os.pathsep + env.get("PATH", "")
        process.spawn([target], input_driver=broker)
        driver = subprocess.run(
            ["/bin/sh", "-c", driver_script],
            env=env,
            capture_output=True,
            text=True,
            timeout=SPAWN_TIMEOUT,
        )
        deadline = time.monotonic() + SPAWN_TIMEOUT
        returncode = None
        while time.monotonic() < deadline:
            returncode = process.poll()
            if returncode is not None:
                break
            time.sleep(0.02)
        process.terminate_tree(grace=1.0)
        process.close()
        return returncode, process.normalized_output(), driver
    finally:
        broker.close()
        process.close()


def test_getpass_is_answered_through_the_helper_despite_tcsaflush(tmp_path):
    """The motivating reproduction: getpass's TCSAFLUSH discards the spawn-time VEOF, so
    without the broker this target blocks until the script timeout. With the broker the
    test waits for the prompt, types the password, and the target exits cleanly."""
    returncode, transcript, driver = run_with_broker(
        tmp_path,
        "getpass_target",
        """
        import getpass

        secret = getpass.getpass("Master password: ")
        print(f"GOT:{secret}")
        """,
        'codeplain-tty wait-for "Master password:" --timeout 15 && codeplain-tty send-text "hunter2\n"',
    )
    assert driver.returncode == 0, driver.stderr
    assert returncode == 0
    assert "GOT:hunter2" in transcript


def test_a_plain_input_read_is_answered_too(tmp_path):
    returncode, transcript, driver = run_with_broker(
        tmp_path,
        "input_target",
        """
        name = input("Name: ")
        print(f"HELLO:{name}")
        """,
        'codeplain-tty wait-for "Name:" --timeout 15 && codeplain-tty send-text "world\n"',
    )
    assert driver.returncode == 0, driver.stderr
    assert returncode == 0
    assert "HELLO:world" in transcript


def test_send_control_delivers_ctrl_d_as_eof(tmp_path):
    returncode, transcript, driver = run_with_broker(
        tmp_path,
        "eof_target",
        """
        import sys

        print("READY", flush=True)
        data = sys.stdin.read()
        print(f"EOF-AFTER:{len(data)}")
        """,
        "codeplain-tty wait-for READY --timeout 15 && codeplain-tty send-control d",
    )
    assert driver.returncode == 0, driver.stderr
    assert returncode == 0
    assert "EOF-AFTER:0" in transcript


def test_size_reaches_the_target_as_sigwinch_and_a_new_size(tmp_path):
    returncode, transcript, driver = run_with_broker(
        tmp_path,
        "size_target",
        """
        import os
        import signal
        import sys

        resized = []

        def on_winch(signum, frame):
            resized.append(os.get_terminal_size(sys.stdout.fileno()))

        signal.signal(signal.SIGWINCH, on_winch)
        print("READY", flush=True)
        while not resized:
            signal.pause()
        print(f"SIZE:{resized[0].columns}x{resized[0].lines}")
        """,
        "codeplain-tty wait-for READY --timeout 15 && codeplain-tty size 100 30",
    )
    assert driver.returncode == 0, driver.stderr
    assert returncode == 0
    assert "SIZE:100x30" in transcript


def test_the_helper_reports_the_runtime_unavailable_outside_a_test_run(tmp_path):
    env = {key: value for key, value in os.environ.items() if not key.startswith(tty_protocol.ENV_VAR_PREFIX)}
    result = subprocess.run(
        [
            sys.executable,
            os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "plain2code_tty.py"),
            "wait-for",
            "x",
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=SPAWN_TIMEOUT,
    )
    assert result.returncode == tty_protocol.EXIT_RUNTIME_UNAVAILABLE
    assert "not available" in result.stderr


def test_a_wait_that_times_out_exits_one(tmp_path):
    returncode, transcript, driver = run_with_broker(
        tmp_path,
        "quiet_target",
        """
        import time

        print("READY", flush=True)
        time.sleep(2)
        """,
        'codeplain-tty wait-for "never-printed" --timeout 1',
    )
    assert driver.returncode == tty_protocol.EXIT_COMMAND_FAILED
    assert "did not" in driver.stderr
