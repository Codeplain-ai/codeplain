"""`codeplain-tty` — the terminal-automation helper for Codeplain's internal tests.

Available on PATH only while Codeplain runs its own conformance and acceptance tests. A
generated test invokes it to drive the tested process through its controlling terminal:
wait for a prompt to appear in the transcript, type text, press a control key, send
exact bytes, or resize the terminal. It talks to a private per-execution broker over the
endpoint named in the environment; outside a Codeplain test run those variables do not
exist and the helper reports the runtime as unavailable.

Exit codes: 0 success; 1 the command ran and did not succeed (a wait that timed out,
input the target no longer accepts); 2 usage error; 69 the runtime itself is
unavailable (missing environment, unreachable broker, protocol mismatch).
"""

import argparse
import os
import socket
import sys
from typing import Optional

from render_machine import tty_protocol

# How much longer than the command's own deadline the helper waits for the response
# frame, so a broker-side wait always resolves before the client gives up on it.
RESPONSE_MARGIN_SECONDS = 10.0

_ERROR_EXIT_CODES = {
    tty_protocol.ERROR_TIMEOUT: tty_protocol.EXIT_COMMAND_FAILED,
    tty_protocol.ERROR_INPUT_CLOSED: tty_protocol.EXIT_COMMAND_FAILED,
    tty_protocol.ERROR_BACKPRESSURE: tty_protocol.EXIT_COMMAND_FAILED,
    tty_protocol.ERROR_UNSUPPORTED: tty_protocol.EXIT_COMMAND_FAILED,
    tty_protocol.ERROR_INVALID_REQUEST: tty_protocol.EXIT_USAGE,
    tty_protocol.ERROR_UNAUTHORIZED: tty_protocol.EXIT_RUNTIME_UNAVAILABLE,
    tty_protocol.ERROR_SHUTTING_DOWN: tty_protocol.EXIT_RUNTIME_UNAVAILABLE,
    tty_protocol.ERROR_INTERNAL: tty_protocol.EXIT_RUNTIME_UNAVAILABLE,
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="codeplain-tty",
        description="Drive the terminal of a process under Codeplain's internal functional tests.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    wait_for = commands.add_parser(tty_protocol.COMMAND_WAIT_FOR, help="wait until TEXT appears in the transcript")
    wait_for.add_argument("text")
    wait_for.add_argument("--timeout", type=float, default=30.0, help="seconds to wait (default: 30)")

    wait_absent = commands.add_parser(
        tty_protocol.COMMAND_WAIT_UNTIL_ABSENT, help="wait until TEXT is no longer in the transcript"
    )
    wait_absent.add_argument("text")
    wait_absent.add_argument("--timeout", type=float, default=30.0, help="seconds to wait (default: 30)")

    send_text = commands.add_parser(
        tty_protocol.COMMAND_SEND_TEXT,
        help="type TEXT into the terminal (newlines are typed as the Enter key)",
    )
    send_text.add_argument("text")

    send_control = commands.add_parser(
        tty_protocol.COMMAND_SEND_CONTROL, help="press Ctrl-KEY (e.g. 'd' for Ctrl-D, 'c' for Ctrl-C)"
    )
    send_control.add_argument("key")

    send_hex = commands.add_parser(tty_protocol.COMMAND_SEND_HEX, help="send exact bytes, hex-encoded")
    send_hex.add_argument("hex")

    size = commands.add_parser(tty_protocol.COMMAND_SIZE, help="resize the terminal")
    size.add_argument("columns", type=int)
    size.add_argument("rows", type=int)

    return parser


def _request_args(options: argparse.Namespace) -> dict:
    if options.command in (tty_protocol.COMMAND_WAIT_FOR, tty_protocol.COMMAND_WAIT_UNTIL_ABSENT):
        return {"text": options.text, "timeout": options.timeout}
    if options.command == tty_protocol.COMMAND_SEND_TEXT:
        return {"text": options.text}
    if options.command == tty_protocol.COMMAND_SEND_CONTROL:
        return {"key": options.key}
    if options.command == tty_protocol.COMMAND_SEND_HEX:
        return {"hex": options.hex}
    return {"columns": options.columns, "rows": options.rows}


def _response_deadline(options: argparse.Namespace) -> float:
    timeout = getattr(options, "timeout", 0.0) or 0.0
    return timeout + RESPONSE_MARGIN_SECONDS


def _fail(message: str, exit_code: int) -> int:
    print(f"codeplain-tty: {message}", file=sys.stderr)
    return exit_code


def run(argv: Optional[list] = None) -> int:
    options = build_parser().parse_args(argv)

    endpoint = os.environ.get(tty_protocol.ENDPOINT_ENV_VAR)
    token = os.environ.get(tty_protocol.TOKEN_ENV_VAR)
    if not endpoint or not token:
        return _fail(
            "the Codeplain platform-test runtime is not available here "
            f"({tty_protocol.ENDPOINT_ENV_VAR} is not set)",
            tty_protocol.EXIT_RUNTIME_UNAVAILABLE,
        )
    if not hasattr(socket, "AF_UNIX"):
        return _fail("this platform's transport is not supported yet", tty_protocol.EXIT_RUNTIME_UNAVAILABLE)

    request = tty_protocol.request(token, options.command, _request_args(options))
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(_response_deadline(options))
            connection.connect(endpoint)
            connection.sendall(tty_protocol.encode_frame(request))
            response = tty_protocol.read_frame(connection.recv)
    except (OSError, tty_protocol.ProtocolError) as exc:
        return _fail(f"could not reach the test runtime broker: {exc}", tty_protocol.EXIT_RUNTIME_UNAVAILABLE)

    if response is None:
        return _fail("the broker closed the connection without answering", tty_protocol.EXIT_RUNTIME_UNAVAILABLE)
    if response.get("ok") is True:
        return tty_protocol.EXIT_OK
    error = str(response.get("error"))
    message = response.get("message", "the command failed")
    return _fail(f"{options.command}: {message}", _ERROR_EXIT_CODES.get(error, tty_protocol.EXIT_RUNTIME_UNAVAILABLE))


def main() -> None:
    sys.exit(run())


if __name__ == "__main__":
    main()
