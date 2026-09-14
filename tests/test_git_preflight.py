"""Regression tests for issue #133.

When git is unusable the CLI must report a clean message and exit 1, never dump
a traceback. The failure is at import time (GitPython raises from module scope
before main() runs), so the CLI is run in a subprocess and asserted on what a
user would actually see. sys.executable is launched by absolute path, so
replacing PATH only controls which git, if any, can be found.
"""

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from git_preflight import GIT_BROKEN_MESSAGE, GIT_MISSING_MESSAGE

REPO_ROOT = Path(__file__).resolve().parents[1]
TRACEBACK_MARKER = "Traceback (most recent call last)"

# The crash was in the import chain, so it hit every entry point equally.
CLI_INVOCATIONS = [["--version"], ["--status"], ["does-not-exist.plain"]]


def run_cli(args, path_dir=None):
    """Run the CLI and return its exit code and output, stripped of styling and
    with whitespace collapsed, so assertions survive console line wrapping."""
    env = os.environ.copy()
    # Drop GitPython overrides so the PATH search decides whether git is found.
    env.pop("GIT_PYTHON_GIT_EXECUTABLE", None)
    env.pop("GIT_PYTHON_REFRESH", None)
    env.setdefault("CODEPLAIN_API_KEY", "dummy")
    if path_dir is not None:
        env["PATH"] = str(path_dir)

    result = subprocess.run(
        [sys.executable, "plain2code.py", *args],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    output = re.sub(r"\x1b\[[0-9;]*m", "", result.stdout + result.stderr)
    return result.returncode, " ".join(output.split())


@pytest.fixture
def no_git_dir(tmp_path):
    """A directory containing no git at all, used as the whole PATH."""
    d = tmp_path / "no-git"
    d.mkdir()
    return d


@pytest.fixture
def broken_git_dir(tmp_path):
    """A directory whose git resolves but always fails, used as the whole PATH.
    This is the shape of macOS without the Command Line Tools."""
    d = tmp_path / "broken-git"
    d.mkdir()
    if sys.platform == "win32":
        (d / "git.bat").write_text("@echo off\nexit /b 127\n")
    else:
        git = d / "git"
        git.write_text("#!/bin/sh\nexit 127\n")
        git.chmod(0o755)
    return d


@pytest.mark.parametrize("args", CLI_INVOCATIONS)
def test_missing_git_reports_cleanly(args, no_git_dir):
    rc, output = run_cli(args, no_git_dir)

    assert GIT_MISSING_MESSAGE in output, output
    assert TRACEBACK_MARKER not in output, output
    # GitPython's own advice about GIT_PYTHON_REFRESH must not reach the user.
    assert "GIT_PYTHON_REFRESH" not in output, output
    assert rc == 1, output


@pytest.mark.parametrize("args", CLI_INVOCATIONS)
def test_broken_git_reports_cleanly(args, broken_git_dir):
    rc, output = run_cli(args, broken_git_dir)

    assert GIT_BROKEN_MESSAGE in output, output
    assert GIT_MISSING_MESSAGE not in output, output
    assert TRACEBACK_MARKER not in output, output
    assert rc == 1, output


def test_cli_works_when_git_is_present():
    rc, output = run_cli(["--version"])

    assert rc == 0, output
    assert "codeplain version" in output, output
