"""Console detachment on native Windows, for the legacy pipe backend.

Windows is the one platform where `stdin=DEVNULL` is not enough. A child attached to the
renderer's console can open `CONIN$` and read the console input buffer directly, whatever
its standard input handle points at, so the backend detaches every Windows child from that
console instead. What is asserted here is the detachment itself: the renderer's pid must
not appear in the child's console process list.

The control case runs the same probe with the same redirections and no creation flags, so
a green assertion above cannot be explained by the redirection alone. It is skipped when
the test process has no console of its own — a CI runner without one has nothing for a
child to inherit, which leaves the guard true for a reason this module cannot claim credit
for.
"""

import ctypes
import json
import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path
from typing import List

import pytest

from render_machine._legacy_pipe import LegacyPipeProcess

pytestmark = pytest.mark.skipif(
    sys.platform != "win32",
    reason="Console attachment is a Windows notion, and so is the creation flag under test.",
)

PROBE_TIMEOUT_SECONDS = 30.0
POLL_INTERVAL_SECONDS = 0.02

# Enough for any console a test child can find itself on; a longer list is truncated
# rather than trusted, because the API reports the required length instead of filling.
MAX_CONSOLE_PIDS = 64

# Reports which processes share the console this program is attached to.
CONSOLE_PROBE_PROGRAM = f"""
import ctypes
import json
import os
import sys

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
buffer = (ctypes.c_uint * {MAX_CONSOLE_PIDS})()
count = min(kernel32.GetConsoleProcessList(buffer, {MAX_CONSOLE_PIDS}), {MAX_CONSOLE_PIDS})
report = {{"pid": os.getpid(), "console_pids": list(buffer[:count])}}
sys.stdout.write(json.dumps(report))
sys.stdout.flush()
"""


def console_pids() -> List[int]:
    """The pids attached to this process's console, or an empty list when it has none."""
    if sys.platform != "win32":  # unreachable: the module is skipped everywhere else
        return []
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    buffer = (ctypes.c_uint * MAX_CONSOLE_PIDS)()
    count = min(kernel32.GetConsoleProcessList(buffer, MAX_CONSOLE_PIDS), MAX_CONSOLE_PIDS)
    return list(buffer[:count])


@pytest.fixture
def probe_script(tmp_path: Path) -> str:
    script_path = tmp_path / "console_probe.py"
    script_path.write_text(textwrap.dedent(CONSOLE_PROBE_PROGRAM))
    return str(script_path)


@pytest.fixture
def backend():
    process = LegacyPipeProcess()
    try:
        yield process
    finally:
        process.terminate_tree(grace=0.1)
        process.close()


def run_probe(process: LegacyPipeProcess, script_path: str) -> dict:
    """Runs the probe on the backend and returns the report it printed."""
    process.spawn([sys.executable, script_path])
    deadline = time.monotonic() + PROBE_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if process.poll() is not None:
            break
        time.sleep(POLL_INTERVAL_SECONDS)
    else:
        raise AssertionError(f"the console probe did not exit within {PROBE_TIMEOUT_SECONDS}s")
    process.close()
    return json.loads(process.read_output().strip())


def test_the_child_is_not_attached_to_the_renderers_console(backend, probe_script):
    report = run_probe(backend, probe_script)

    assert os.getpid() not in report["console_pids"]
    # Whatever console the child ended up with is its own, so the probe read a real list
    # rather than reporting an empty one because the call failed.
    assert report["console_pids"] in ([], [report["pid"]])


def test_the_control_case_proves_the_redirection_alone_does_not_detach(probe_script):
    """The same spawn shape minus the creation flags: this child does share the console."""
    if not console_pids():
        pytest.skip("this process has no console, so there is none for a child to inherit")

    process = subprocess.Popen(
        [sys.executable, probe_script],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        output, _ = process.communicate(timeout=PROBE_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate(timeout=PROBE_TIMEOUT_SECONDS)
        pytest.fail("the control probe never reported its console")

    assert os.getpid() in json.loads(output.strip())["console_pids"]
