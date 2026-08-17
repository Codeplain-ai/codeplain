"""The wire contract between the `codeplain-tty` helper and the per-execution broker.

One request per connection: the helper connects to the endpoint named by
``CODEPLAIN_TTY_ENDPOINT``, sends one length-framed JSON request carrying the token from
``CODEPLAIN_TTY_TOKEN``, reads one length-framed JSON response, and disconnects. The
framing, the field names, and the command vocabulary here ARE protocol version 1 — the
same version the client advertises to the API — so nothing in this module may change
without a new protocol version.

This module is imported by both sides (the broker inside the renderer and the helper
process a generated test spawns), so it stays dependency-free: standard library only,
and no imports from the rest of the codebase.
"""

import json
import struct
from typing import Optional, Tuple

PROTOCOL_VERSION = 1

ENDPOINT_ENV_VAR = "CODEPLAIN_TTY_ENDPOINT"
TOKEN_ENV_VAR = "CODEPLAIN_TTY_TOKEN"

# Every environment variable the runtime owns starts with this prefix. Scoping strips
# caller-supplied values and the portability audit rejects references outside internal
# test folders by this prefix, so it is defined once, here.
ENV_VAR_PREFIX = "CODEPLAIN_TTY_"

COMMAND_WAIT_FOR = "wait-for"
COMMAND_WAIT_UNTIL_ABSENT = "wait-until-absent"
COMMAND_SEND_TEXT = "send-text"
COMMAND_SEND_CONTROL = "send-control"
COMMAND_SEND_HEX = "send-hex"
COMMAND_SIZE = "size"

COMMANDS = (
    COMMAND_WAIT_FOR,
    COMMAND_WAIT_UNTIL_ABSENT,
    COMMAND_SEND_TEXT,
    COMMAND_SEND_CONTROL,
    COMMAND_SEND_HEX,
    COMMAND_SIZE,
)

# Error codes a response can carry. The helper maps them onto its exit codes.
ERROR_UNAUTHORIZED = "unauthorized"
ERROR_UNSUPPORTED = "unsupported"
ERROR_INVALID_REQUEST = "invalid-request"
ERROR_TIMEOUT = "timeout"
ERROR_INPUT_CLOSED = "input-closed"
ERROR_BACKPRESSURE = "backpressure"
ERROR_SHUTTING_DOWN = "shutting-down"
ERROR_INTERNAL = "internal"

# Helper exit codes. 0 is success; 1 is a command that ran and did not succeed (a
# wait-for that timed out, input the target no longer accepts); 2 is a usage error the
# caller can fix; 69 is the runtime itself being unavailable — matching the testing
# scripts' convention that 69 is an environment failure, never a test failure.
EXIT_OK = 0
EXIT_COMMAND_FAILED = 1
EXIT_USAGE = 2
EXIT_RUNTIME_UNAVAILABLE = 69

# One frame: 4-byte big-endian payload length, then that many bytes of UTF-8 JSON.
_HEADER = struct.Struct(">I")
MAX_FRAME_BYTES = 64 * 1024


class ProtocolError(Exception):
    """A frame or payload the peer must not act on."""


def encode_frame(payload: dict) -> bytes:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    if len(body) > MAX_FRAME_BYTES:
        raise ProtocolError(f"frame of {len(body)} bytes exceeds the {MAX_FRAME_BYTES}-byte bound")
    return _HEADER.pack(len(body)) + body


def read_frame(recv) -> Optional[dict]:
    """Reads one frame from `recv(max_bytes) -> bytes`. None on a clean end of stream."""
    header = _read_exactly(recv, _HEADER.size)
    if header is None:
        return None
    (length,) = _HEADER.unpack(header)
    if length > MAX_FRAME_BYTES:
        raise ProtocolError(f"frame of {length} bytes exceeds the {MAX_FRAME_BYTES}-byte bound")
    body = _read_exactly(recv, length)
    if body is None:
        raise ProtocolError("the stream ended inside a frame")
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProtocolError(f"the frame does not carry UTF-8 JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ProtocolError("the frame must carry a JSON object")
    return payload


def _read_exactly(recv, count: int) -> Optional[bytes]:
    data = bytearray()
    while len(data) < count:
        chunk = recv(count - len(data))
        if not chunk:
            if not data:
                return None  # a clean end of stream, before anything was read
            raise ProtocolError("the stream ended inside a frame")
        data += chunk
    return bytes(data)


def request(token: str, command: str, args: dict) -> dict:
    return {"protocol_version": PROTOCOL_VERSION, "token": token, "command": command, "args": args}


def ok_response(result: Optional[dict] = None) -> dict:
    return {"ok": True, "result": result or {}}


def error_response(code: str, message: str) -> dict:
    return {"ok": False, "error": code, "message": message}


def control_byte(key: str) -> bytes:
    """The control byte an ASCII Ctrl-<key> keypress produces (Ctrl-D -> 0x04)."""
    if len(key) != 1:
        raise ValueError("send-control takes a single character, e.g. 'd' for Ctrl-D")
    upper = key.upper()
    code = ord(upper) ^ 0x40
    if not 0 <= code <= 0x1F:
        raise ValueError(f"'{key}' does not name a control character")
    return bytes([code])


def typed_text_bytes(text: str) -> bytes:
    """What typing `text` at a terminal sends: newlines become carriage returns.

    A terminal's Enter key sends CR; the line discipline's ICRNL turns it back into the
    newline a canonical read returns. Sending LF verbatim would bypass what every
    interactive program is written against, so `send-text` emulates typing. `send-hex`
    exists for exact bytes.
    """
    return text.replace("\r\n", "\n").replace("\n", "\r").encode("utf-8")


def parse_size_args(args: dict) -> Tuple[int, int]:
    columns, rows = args.get("columns"), args.get("rows")
    if not isinstance(columns, int) or not isinstance(rows, int) or columns <= 0 or rows <= 0:
        raise ValueError("size takes positive integer columns and rows")
    return columns, rows
