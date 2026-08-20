"""The ConPTY backend reached through `execute_script()`.

These cases run the real renderer path instead of constructing a backend directly, so they
only mean anything once script execution is routed through `TerminalProcess`. That is why
they live apart from `test_conpty.py`: that module tests the backend, this one tests that
the backend is what execution actually reaches.

Windows-only, like the backend itself.
"""

import os
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest

if sys.platform != "win32":
    # The backend binds kernel32 at import time, so collection has to stop here rather than
    # leaving the cases to a skip mark.
    pytest.skip("The ConPTY backend is not built off Windows.", allow_module_level=True)

from plain2code_exceptions import RenderCancelledError  # noqa: E402
from render_machine import _conpty  # noqa: E402
from render_machine import render_utils  # noqa: E402
from render_machine.terminal_process import ENVIRONMENT_ERROR_EXIT_CODE  # noqa: E402

WAIT_TIMEOUT = 30.0
POLL = 0.05

# The backend's own binding, so a patched symbol is the one production code calls.
kernel32 = _conpty.kernel32

SCRIPT_TYPE = "Unit"


def wait_for(predicate, timeout=WAIT_TIMEOUT):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(POLL)
    return bool(predicate())


def write_powershell(tmp_path: Path, name: str, body: str) -> str:
    path = tmp_path / f"{name}.ps1"
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    return str(path)


def run_script(script: str, timeout: int, stop_event=None):
    """One execution through the real renderer path, artifacts cleaned up afterwards."""
    exit_code, output, artifact = render_utils.execute_script(
        script, [], SCRIPT_TYPE, timeout=timeout, stop_event=stop_event
    )
    if artifact is not None:
        for path in (artifact, artifact + render_utils.RAW_OUTPUT_SUFFIX):
            try:
                os.unlink(path)
            except OSError:
                pass
    return exit_code, output


REPORTING_SCRIPT = """
    Write-Output "HELLO-CONPTY"
    exit 0
"""

FAILING_SCRIPT = """
    Write-Output "BEFORE-EXIT"
    exit 3
"""

SINGLE_READ_SCRIPT = """
    Write-Output "READY"
    $line = [Console]::In.ReadLine()
    Write-Output "GOT $line"
"""

REPEATED_READ_SCRIPT = """
    Write-Output "READY"
    while ($true) {
        $line = [Console]::In.ReadLine()
        Write-Output "GOT $line"
    }
"""


def test_a_powershell_script_runs_through_execute_script_and_reports_its_output(tmp_path):
    script = write_powershell(tmp_path, "reports", REPORTING_SCRIPT)

    exit_code, output = run_script(script, timeout=90)

    assert exit_code == 0
    assert "HELLO-CONPTY" in output


def test_a_failing_powershell_script_returns_its_exit_code_verbatim(tmp_path):
    script = write_powershell(tmp_path, "fails", FAILING_SCRIPT)

    exit_code, output = run_script(script, timeout=90)

    assert exit_code == 3
    assert "BEFORE-EXIT" in output


@pytest.mark.parametrize(
    "name,body",
    [("reads_once", SINGLE_READ_SCRIPT), ("reads_repeatedly", REPEATED_READ_SCRIPT)],
)
def test_a_script_that_reads_input_runs_to_the_timeout_and_says_why(tmp_path, name, body):
    """The documented Windows asymmetry: ConPTY carries no synthetic end-of-file, so a read
    blocks until the timeout instead of returning EOF the way it does on POSIX."""
    script = write_powershell(tmp_path, name, body)

    exit_code, output = run_script(script, timeout=15)

    assert exit_code == render_utils.TIMEOUT_ERROR_EXIT_CODE
    assert "no synthetic end-of-file" in output.lower()
    assert "end-of-file" in output.lower()


def test_a_cancelled_script_raises_instead_of_publishing_an_outcome(tmp_path):
    """Cancellation while the script is blocked on a read it will never satisfy: the run
    raises rather than waiting out the timeout it would otherwise reach."""
    marker = tmp_path / "started.txt"
    script = write_powershell(
        tmp_path,
        "cancelled_read",
        f"""
        New-Item -ItemType File -Path "{marker}" | Out-Null
        Write-Output "READY"
        $line = [Console]::In.ReadLine()
        Write-Output "GOT $line"
        """,
    )
    stop = threading.Event()
    watcher = threading.Thread(target=lambda: stop.set() if wait_for(marker.exists, timeout=90.0) else None)
    watcher.daemon = True
    watcher.start()

    started = time.monotonic()
    with pytest.raises(RenderCancelledError):
        run_script(script, timeout=180, stop_event=stop)

    assert time.monotonic() - started < 90.0  # cancelled, not timed out
    watcher.join(5.0)


@pytest.mark.parametrize(
    "symbol,result",
    [
        ("WaitForSingleObject", 0xFFFFFFFF),  # WAIT_FAILED
        ("GetExitCodeProcess", 0),
        ("QueryInformationJobObject", 0),
        ("TerminateJobObject", 0),
    ],
)
def test_a_native_call_failing_after_launch_is_an_environment_error(monkeypatch, tmp_path, symbol, result):
    """The post-launch seams: a wait, an exit-code read, a job query or a job termination that
    fails describes a run whose outcome nobody could observe, so it takes the 69 channel rather
    than being reported as a timeout or a clean pass."""
    script = write_powershell(tmp_path, "reports", REPORTING_SCRIPT)
    monkeypatch.setattr(kernel32, symbol, lambda *arguments: result)

    exit_code, output = run_script(script, timeout=90)

    assert exit_code == ENVIRONMENT_ERROR_EXIT_CODE
    assert "could not be executed" in output
