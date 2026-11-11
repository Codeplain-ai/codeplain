"""What the fixers are told when a test script runs out of time."""

import os
import stat
import sys

from render_machine import render_utils


def test_a_timed_out_script_tells_the_fixer_to_find_the_blocking_test(tmp_path):
    # execute_script runs only PowerShell scripts on Windows.
    if sys.platform == "win32":
        script = tmp_path / "hang.ps1"
        script.write_text("Write-Output starting\nStart-Sleep -Seconds 30\n")
    else:
        script = tmp_path / "hang.sh"
        script.write_text("#!/bin/bash\necho starting\nsleep 30\n")
        script.chmod(script.stat().st_mode | stat.S_IEXEC)

    exit_code, issue, log_path = render_utils.execute_script(str(script), [], "Conformance Tests", timeout=1)

    assert exit_code == render_utils.TIMEOUT_ERROR_EXIT_CODE
    assert "did not finish in 1 seconds" in issue and "a test that blocks" in issue
    assert os.path.isfile(log_path)
