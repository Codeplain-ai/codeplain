"""Tests for running test scripts with render_utils.execute_script."""

import os
import stat
import sys
from pathlib import Path

import pytest

from render_machine.render_utils import execute_script


@pytest.fixture
def stdin_with_input():
    """Replace fd 0 with a pipe holding a line, as a terminal would after a keypress.

    A pipe stands in for the terminal: whether a script can reach codeplain's stdin
    does not depend on what kind of file stdin is, and a pipe works in CI too.
    """
    read_fd, write_fd = os.pipe()
    os.write(write_fd, b"keypress\n")
    os.close(write_fd)
    # A child process inherits file descriptor 0, not sys.stdin, so fd 0 is what has to change.
    saved_stdin = os.dup(0)
    os.dup2(read_fd, 0)
    os.close(read_fd)
    yield
    os.dup2(saved_stdin, 0)
    os.close(saved_stdin)


@pytest.mark.skipif(sys.platform == "win32", reason="shell scripts are not supported on Windows")
def test_script_does_not_inherit_stdin(tmp_path: Path, stdin_with_input):
    # A tool such as flutter resets the terminal it reads from, which breaks the TUI.
    # The script reports what it finds on its stdin: the waiting line if it inherited
    # codeplain's stdin, end-of-file if it was given an empty one.
    script = tmp_path / "read_stdin.sh"
    script.write_text('#!/bin/sh\nif read -r line; then echo "read:$line"; else echo "eof"; fi\n')
    script.chmod(script.stat().st_mode | stat.S_IEXEC)

    returncode, output, _ = execute_script(str(script), [], "Unit Tests")

    assert returncode == 0
    assert output.strip() == "eof"
