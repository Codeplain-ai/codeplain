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

import functools
import os
import subprocess
from typing import Optional

from plain2code_console import console
from render_machine import tty_protocol
from render_machine.terminal_process import TerminalProcess
from render_machine.tty_broker import TtyBroker, broker_supported

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


PREFLIGHT_MARKER = "codeplain-tty-preflight"
PREFLIGHT_TIMEOUT_SECONDS = 30.0


class _PreflightProbe(TerminalProcess):
    """A stand-in target whose transcript already contains the preflight marker."""

    def normalized_output(self) -> str:
        return PREFLIGHT_MARKER


@functools.lru_cache(maxsize=1)
def platform_test_runtime_available() -> bool:
    """One real round trip through the runtime, cached for the process's lifetime.

    Support is never derived from a version: the capability is advertised only after the
    broker starts, installs its helper, and the helper — executed exactly the way a
    generated test will execute it — authenticates and completes a command over the
    socket. Any failure keeps the runtime off and the request un-advertised.
    """
    if not broker_supported():
        return False
    broker = None
    try:
        broker = TtyBroker(_PreflightProbe())
        broker.start()
        env = {key: value for key, value in os.environ.items() if not key.startswith(tty_protocol.ENV_VAR_PREFIX)}
        env.update(broker.child_env())
        assert broker.helper_bin_dir is not None
        helper = os.path.join(broker.helper_bin_dir, "codeplain-tty")
        result = subprocess.run(
            [helper, "wait-for", PREFLIGHT_MARKER, "--timeout", "5"],
            env=env,
            capture_output=True,
            timeout=PREFLIGHT_TIMEOUT_SECONDS,
        )
        if result.returncode != 0:
            console.debug(f"codeplain-tty preflight failed (exit {result.returncode}): {result.stderr!r}")
        return result.returncode == 0
    except Exception as exc:
        console.debug(f"codeplain-tty preflight failed: {exc!r}")
        return False
    finally:
        if broker is not None:
            broker.close()


def advertised_platform_test_runtime() -> Optional[dict]:
    """What the client actually advertises: the descriptor, or None while it cannot.

    None keeps the API on its backward-compatible path — no `codeplain-tty` prompt
    content is generated for a runtime this client could not provide.
    """
    return codeplain_tty_descriptor() if platform_test_runtime_available() else None
