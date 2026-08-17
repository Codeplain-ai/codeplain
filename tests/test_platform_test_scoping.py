"""Tests for the platform-test runtime's execution scoping in `execute_script()`.

The runtime is an explicit per-execution option, never a process-global switch: only an
execution that asks for it gets the broker, the helper on PATH, and the scoped
CODEPLAIN_TTY_* environment — and an execution that does not ask sees none of it, even
when the caller's own environment carries stale values. The closing case is the
acceptance gate that motivated the whole plan: the getpass/TCSAFLUSH reproduction
passing through the real `execute_script()` path.
"""

import stat
import sys
import textwrap
from pathlib import Path

import pytest

from render_machine import render_utils, tty_protocol

posix_only = pytest.mark.skipif(
    sys.platform == "win32",
    reason="These cases run POSIX shell scripts and the POSIX-only broker transport.",
)

pytestmark = posix_only


def make_script(directory: Path, name: str, body: str) -> str:
    script_path = directory / f"{name}.sh"
    script_path.write_text("#!/bin/bash\n" + textwrap.dedent(body))
    script_path.chmod(script_path.stat().st_mode | stat.S_IXUSR)
    return str(script_path)


def test_a_plain_execution_gets_no_runtime_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEPLAIN_TTY_ENDPOINT", "/stale/endpoint")
    script = make_script(
        tmp_path,
        "probe",
        """
        if command -v codeplain-tty >/dev/null 2>&1; then echo "HELPER-ON-PATH"; fi
        echo "ENDPOINT:${CODEPLAIN_TTY_ENDPOINT:-unset}"
        """,
    )

    exit_code, output, _ = render_utils.execute_script(script, [], "Repro", timeout=30)

    assert exit_code == 0
    assert "HELPER-ON-PATH" not in output
    # The stale caller value still reaches a plain execution untouched (today's
    # behavior); only the scoped runtime owns and rewrites the prefix.
    assert "ENDPOINT:/stale/endpoint" in output


def test_a_runtime_execution_gets_the_helper_and_a_scoped_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEPLAIN_TTY_ENDPOINT", "/stale/endpoint")
    monkeypatch.setenv("CODEPLAIN_TTY_TOKEN", "stale-token")
    script = make_script(
        tmp_path,
        "scoped_probe",
        """
        command -v codeplain-tty >/dev/null 2>&1 || { echo "NO-HELPER"; exit 1; }
        [ "${CODEPLAIN_TTY_ENDPOINT}" = "/stale/endpoint" ] && { echo "STALE-ENDPOINT"; exit 1; }
        [ "${CODEPLAIN_TTY_TOKEN}" = "stale-token" ] && { echo "STALE-TOKEN"; exit 1; }
        [ -S "${CODEPLAIN_TTY_ENDPOINT}" ] || { echo "ENDPOINT-NOT-A-SOCKET"; exit 1; }
        echo "SCOPED-OK"
        """,
    )

    exit_code, output, _ = render_utils.execute_script(script, [], "Repro", timeout=30, platform_test_runtime=True)

    assert exit_code == 0, output
    assert "SCOPED-OK" in output


def test_the_getpass_reproduction_passes_through_the_real_execution_path(tmp_path):
    """The acceptance gate: a conformance-style script feeds a getpass child through
    codeplain-tty instead of hanging to the 120-second timeout on the discarded VEOF."""
    child = tmp_path / "child_getpass.py"
    child.write_text(textwrap.dedent("""
            import getpass

            secret = getpass.getpass("Master password: ")
            print(f"GOT:{secret}")
            """))
    script = make_script(
        tmp_path,
        "conformance_style",
        f"""
        "{sys.executable}" "{child}" &
        target=$!
        codeplain-tty wait-for "Master password:" --timeout 15 || exit 1
        codeplain-tty send-text "hunter2
        " || exit 1
        wait "$target"
        """,
    )

    exit_code, output, _ = render_utils.execute_script(script, [], "Repro", timeout=60, platform_test_runtime=True)

    assert exit_code == 0, output
    assert "GOT:hunter2" in output


def test_a_runtime_execution_that_cannot_start_its_broker_is_an_environment_error(tmp_path, monkeypatch):
    def refuse_to_start(self):
        raise OSError("no sockets today")

    monkeypatch.setattr(render_utils.TtyBroker, "start", refuse_to_start)
    script = make_script(tmp_path, "never_runs", 'echo "MUST-NOT-RUN"\n')

    exit_code, output, _ = render_utils.execute_script(script, [], "Repro", timeout=30, platform_test_runtime=True)

    assert exit_code == tty_protocol.EXIT_RUNTIME_UNAVAILABLE
    assert "MUST-NOT-RUN" not in output
