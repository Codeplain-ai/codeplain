"""The per-execution broker behind the `codeplain-tty` helper.

One broker serves one script execution: created after the terminal backend and before
the target is spawned, so its endpoint and token can be placed in the child environment,
and closed before `execute_script()` returns, taking every filesystem artifact with it.
A generated conformance or acceptance test drives the target's terminal through it —
`wait-for` observes the renderer-owned transcript without consuming it, the `send-*`
commands enqueue bytes on the backend's ordered input queue, and `size` resizes the
live terminal.

Security model: the endpoint is a Unix-domain socket inside a fresh mode-0700 directory,
and every request must carry the per-execution token, compared in constant time. The
token travels only through the scoped child environment — never on a command line, never
in a log line, never in a prompt. Native Windows uses a named pipe transport, which is
not implemented yet; the capability is simply not advertised there.

Everything a client can make the broker do is bounded: frame sizes by the protocol,
waits by a capped deadline, sends by a delivery deadline, and the accept loop serves one
request at a time so a flood of connections queues in the listener's backlog instead of
growing threads.
"""

import hmac
import os
import secrets
import shutil
import socket
import sys
import tempfile
import threading
import time
from typing import Optional

from plain2code_console import console
from render_machine import tty_protocol
from render_machine.terminal_process import (
    InputDisposition,
    TerminalInputDriver,
    TerminalProcess,
    TerminalProcessError,
)

# The longest a single wait-for / wait-until-absent may block, whatever the client asks
# for. Sits under the script-execution timeout so a test that waits forever fails as a
# test before the whole script is torn down around it.
MAX_WAIT_SECONDS = 110.0
DEFAULT_WAIT_SECONDS = 30.0

# How long a send-* retries around backpressure before reporting it. The input queue
# drains at terminal speed, so sustained backpressure this long means the target stopped
# reading its terminal.
SEND_DEADLINE_SECONDS = 10.0

# How long the accept loop lets one client take to deliver its request frame. The
# response side is bounded by the command's own deadline.
REQUEST_READ_TIMEOUT_SECONDS = 5.0

POLL_INTERVAL_SECONDS = 0.05

# How long close() waits for the server thread after closing the listener under it.
CLOSE_JOIN_SECONDS = 5.0


def broker_supported() -> bool:
    """True where the transport exists: Unix-domain sockets on POSIX (and WSL)."""
    return sys.platform != "win32" and hasattr(socket, "AF_UNIX")


class TtyBroker(TerminalInputDriver):
    """One execution's terminal-automation endpoint. Single-use, like the backend it drives."""

    def __init__(self, process: TerminalProcess) -> None:
        self._process = process
        self._token = secrets.token_hex(16)
        self._closing = threading.Event()
        # Transcript position consumed by the last successful wait-for. Mutated only by
        # the single server thread, which serves one request at a time.
        self._match_cursor = 0
        self._server: Optional[threading.Thread] = None
        self._listener: Optional[socket.socket] = None
        self._directory: Optional[str] = None
        self.endpoint: Optional[str] = None
        # A directory holding a `codeplain-tty` executable, for prepending to the child's
        # PATH. Written per execution so a source checkout, an editable install, and a
        # built wheel all resolve the same way, and cleaned up with everything else.
        self.helper_bin_dir: Optional[str] = None

    # ------------------------------------------------------------------ lifecycle

    def start(self) -> None:
        if not broker_supported():
            raise TerminalProcessError("the codeplain-tty broker transport is not available on this platform")
        if self._server is not None:
            raise RuntimeError("TtyBroker instances are single-use")
        self._directory = tempfile.mkdtemp(prefix="codeplain-tty-")
        os.chmod(self._directory, 0o700)
        self.endpoint = os.path.join(self._directory, "broker.sock")
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            listener.bind(self.endpoint)
            listener.listen(8)
        except OSError:
            listener.close()
            self._remove_artifacts()
            raise
        self._install_helper()
        self._listener = listener
        self._server = threading.Thread(target=self._serve, name="codeplain-tty-broker", daemon=True)
        self._server.start()

    def _install_helper(self) -> None:
        """Writes the `codeplain-tty` executable the child resolves from its PATH.

        A shim onto this interpreter and this checkout's module, rather than a console
        entry point looked up on the parent's PATH: the helper a test runs must be the
        one matching the broker that is serving it, whatever way Codeplain was installed.
        """
        assert self._directory is not None
        module = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "codeplain_tty.py")
        bin_dir = os.path.join(self._directory, "bin")
        os.makedirs(bin_dir)
        helper = os.path.join(bin_dir, "codeplain-tty")
        with open(helper, "w", encoding="utf-8") as shim:
            shim.write(f'#!/bin/sh\nexec "{sys.executable}" "{module}" "$@"\n')
        os.chmod(helper, 0o700)
        self.helper_bin_dir = bin_dir

    def child_env(self) -> dict:
        """The two variables the helper reads. Scoped to broker-enabled executions only."""
        assert self.endpoint is not None, "start() must succeed before the child environment exists"
        return {
            tty_protocol.ENDPOINT_ENV_VAR: self.endpoint,
            tty_protocol.TOKEN_ENV_VAR: self._token,
        }

    def description(self) -> str:
        return "the codeplain-tty broker is attached to the script's terminal"

    def close(self) -> None:
        """Stops accepting, unblocks the server thread, and removes every artifact. Idempotent."""
        self._closing.set()
        listener = self._listener
        if listener is not None:
            self._listener = None
            try:
                listener.close()  # unblocks accept() with an error the loop expects
            except OSError:
                pass
        server = self._server
        if server is not None and server.ident is not None:
            server.join(timeout=CLOSE_JOIN_SECONDS)
            if server.is_alive():
                console.debug("the codeplain-tty broker thread outlived its shutdown bound")
        self._remove_artifacts()

    def _remove_artifacts(self) -> None:
        directory = self._directory
        self._directory = None
        if directory is not None:
            shutil.rmtree(directory, ignore_errors=True)

    # ------------------------------------------------------------------ the server

    def _serve(self) -> None:
        listener = self._listener
        assert listener is not None
        while not self._closing.is_set():
            try:
                connection, _ = listener.accept()
            except OSError:  # the listener was closed under the loop — expected shutdown
                return
            try:
                self._serve_connection(connection)
            except Exception as exc:  # one bad client must not take the broker down
                console.debug(f"codeplain-tty broker: a connection failed: {exc!r}")
            finally:
                try:
                    connection.close()
                except OSError:
                    pass

    def _serve_connection(self, connection: socket.socket) -> None:
        connection.settimeout(REQUEST_READ_TIMEOUT_SECONDS)
        try:
            request = tty_protocol.read_frame(connection.recv)
        except (tty_protocol.ProtocolError, socket.timeout) as exc:
            self._respond(connection, tty_protocol.error_response(tty_protocol.ERROR_INVALID_REQUEST, str(exc)))
            return
        if request is None:
            return  # the client connected and left
        connection.settimeout(None)  # the command's own deadline bounds the rest
        self._respond(connection, self._handle(request))

    def _respond(self, connection: socket.socket, response: dict) -> None:
        try:
            connection.sendall(tty_protocol.encode_frame(response))
        except OSError:
            pass  # the client is gone; its exit code is its own problem

    # ------------------------------------------------------------------ commands

    def _handle(self, request: dict) -> dict:
        token = request.get("token")
        if not isinstance(token, str) or not hmac.compare_digest(token, self._token):
            return tty_protocol.error_response(tty_protocol.ERROR_UNAUTHORIZED, "the request token is not valid")
        if request.get("protocol_version") != tty_protocol.PROTOCOL_VERSION:
            return tty_protocol.error_response(
                tty_protocol.ERROR_UNSUPPORTED,
                f"this broker speaks protocol version {tty_protocol.PROTOCOL_VERSION}",
            )
        if self._closing.is_set():
            return tty_protocol.error_response(tty_protocol.ERROR_SHUTTING_DOWN, "the execution is shutting down")
        command = request.get("command")
        args = request.get("args")
        if not isinstance(args, dict):
            return tty_protocol.error_response(tty_protocol.ERROR_INVALID_REQUEST, "args must be an object")
        try:
            if command == tty_protocol.COMMAND_WAIT_FOR:
                return self._wait(args, present=True)
            if command == tty_protocol.COMMAND_WAIT_UNTIL_ABSENT:
                return self._wait(args, present=False)
            if command == tty_protocol.COMMAND_SEND_TEXT:
                return self._send(tty_protocol.typed_text_bytes(self._text_arg(args)))
            if command == tty_protocol.COMMAND_SEND_CONTROL:
                return self._send(tty_protocol.control_byte(self._text_arg(args, key="key")))
            if command == tty_protocol.COMMAND_SEND_HEX:
                return self._send(bytes.fromhex(self._text_arg(args, key="hex")))
            if command == tty_protocol.COMMAND_SIZE:
                return self._resize(args)
        except ValueError as exc:
            return tty_protocol.error_response(tty_protocol.ERROR_INVALID_REQUEST, str(exc))
        except Exception as exc:  # a command bug is the broker's failure, not the client's
            console.debug(f"codeplain-tty broker: command {command!r} failed: {exc!r}")
            return tty_protocol.error_response(tty_protocol.ERROR_INTERNAL, f"the broker failed: {exc}")
        return tty_protocol.error_response(tty_protocol.ERROR_INVALID_REQUEST, f"unknown command: {command!r}")

    @staticmethod
    def _text_arg(args: dict, key: str = "text") -> str:
        value = args.get(key)
        if not isinstance(value, str):
            raise ValueError(f"'{key}' must be a string")
        return value

    def _wait(self, args: dict, present: bool) -> dict:
        # The transcript is rendered per line with trailing whitespace stripped, so a
        # needle quoting a prompt verbatim — "Master password: " — could never match as
        # written. End-of-line whitespace is stripped from the needle to compensate.
        text = "\n".join(part.rstrip() for part in self._text_arg(args).split("\n"))
        if not text:
            raise ValueError("the text to wait for must not be empty or whitespace-only")
        timeout = args.get("timeout", DEFAULT_WAIT_SECONDS)
        if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout <= 0:
            raise ValueError("'timeout' must be a positive number of seconds")
        deadline = time.monotonic() + min(float(timeout), MAX_WAIT_SECONDS)
        while True:
            # Expect-style sequencing: a successful wait-for consumes the transcript
            # through its match, so every later wait matches only output produced after
            # it. Without the cursor, the second of two sequential interactive targets
            # matches the first one's stale prompt instantly and the test types into a
            # terminal nobody is reading yet. The transcript re-renders as the screen
            # changes, so the cursor is clamped rather than trusted exactly.
            transcript = self._process.normalized_output()
            start = min(self._match_cursor, len(transcript))
            found_at = transcript.find(text, start)
            if (found_at >= 0) == present:
                if found_at >= 0:
                    self._match_cursor = found_at + len(text)
                return tty_protocol.ok_response()
            if self._closing.is_set():
                return tty_protocol.error_response(tty_protocol.ERROR_SHUTTING_DOWN, "the execution is shutting down")
            if time.monotonic() >= deadline:
                condition = "appear in" if present else "leave"
                return tty_protocol.error_response(
                    tty_protocol.ERROR_TIMEOUT, f"the text did not {condition} the transcript in time"
                )
            time.sleep(POLL_INTERVAL_SECONDS)

    def _send(self, data: bytes) -> dict:
        if not data:
            raise ValueError("there are no bytes to send")
        deadline = time.monotonic() + SEND_DEADLINE_SECONDS
        while True:
            result = self._process.write_input(data)
            if result.disposition is InputDisposition.ACCEPTED:
                return tty_protocol.ok_response({"accepted_bytes": result.accepted_bytes})
            if result.disposition is InputDisposition.CLOSED:
                return tty_protocol.error_response(
                    tty_protocol.ERROR_INPUT_CLOSED, "the target's terminal no longer accepts input"
                )
            if self._closing.is_set() or time.monotonic() >= deadline:
                return tty_protocol.error_response(
                    tty_protocol.ERROR_BACKPRESSURE, "the target stopped reading its terminal input"
                )
            time.sleep(POLL_INTERVAL_SECONDS)

    def _resize(self, args: dict) -> dict:
        columns, rows = tty_protocol.parse_size_args(args)
        try:
            self._process.resize(columns, rows)
        except TerminalProcessError as exc:
            return tty_protocol.error_response(tty_protocol.ERROR_UNSUPPORTED, str(exc))
        return tty_protocol.ok_response()
