"""The platform-test runtime capability the client advertises to the API.

The `codeplain-tty` helper lets a generated conformance or acceptance test drive the
target through its controlling terminal. The API injects instructions about the helper
only into requests that advertise this capability, so the descriptor here is a contract:
protocol version 1 and exactly the commands the client's broker implements. Support is
never derived from a client version — a client advertises only after its broker and
executable pass a local preflight, which is why the gate below is separate from the
descriptor it guards.

The descriptor carries no secrets. The broker endpoint, its authentication token, and
every filesystem or pipe name stay client-side, scoped to one script execution.
"""

from typing import Optional

PROTOCOL_VERSION = 1

# The commands protocol version 1 promises. The API rejects a descriptor naming a command
# outside this set, so the tuple changes only together with the protocol version.
CODEPLAIN_TTY_COMMANDS = (
    "wait-for",
    "wait-until-absent",
    "send-text",
    "send-control",
    "send-hex",
    "size",
)


def codeplain_tty_descriptor() -> dict:
    """The version-1 capability object, as the request models carry it."""
    return {
        "codeplain_tty": {
            "protocol_version": PROTOCOL_VERSION,
            "commands": list(CODEPLAIN_TTY_COMMANDS),
        }
    }


def advertised_platform_test_runtime() -> Optional[dict]:
    """What the client actually advertises: the descriptor, or None while it cannot.

    None keeps the API on its backward-compatible path — no `codeplain-tty` prompt
    content is generated. The broker and executable preflight that turns this on ships
    with the broker itself; until then the client never advertises a runtime it could
    not provide.
    """
    return None
