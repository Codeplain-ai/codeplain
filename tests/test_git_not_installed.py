"""Regression tests for issue #133.

When git is not installed the CLI must report a clean, one-line message and exit
non-zero — never dump an uncaught Python traceback.

The original failure happened at *import time*: GitPython probes for the git
executable the first time it is imported, which raised ``ImportError`` before
``main()`` could handle it. The earlier fix converted that into a nicer
exception type but still raised it at import time, so the traceback persisted.

These tests run the CLI in a subprocess with a git-less ``PATH`` so the real
import + startup path is exercised exactly as a user without git would hit it.
``sys.executable`` is launched by absolute path, so an empty ``PATH`` does not
stop Python from starting — it only makes the git binary unfindable, which is
what ``shutil.which('git')`` and GitPython both key off.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

GIT_MISSING_MESSAGE = "git is not installed. Please install git and try again."


def _env(empty_bin=None):
    """A copy of the current environment with git made findable or not.

    Any explicit GitPython overrides are dropped so the ``PATH`` search is the
    only thing that decides whether git can be found.
    """
    env = os.environ.copy()
    env.pop("GIT_PYTHON_GIT_EXECUTABLE", None)
    env.pop("GIT_PYTHON_REFRESH", None)
    if empty_bin is not None:
        env["PATH"] = str(empty_bin)
    env.setdefault("CODEPLAIN_API_KEY", "dummy")
    return env


def _run_cli(args, env):
    result = subprocess.run(
        [sys.executable, "plain2code.py", *args],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    return result.returncode, result.stdout + result.stderr


@pytest.fixture
def empty_bin(tmp_path):
    """A directory with no git binary, used as the whole PATH."""
    d = tmp_path / "emptybin"
    d.mkdir()
    return d


@pytest.mark.parametrize("args", [["--version"], ["--status"], ["does-not-exist.plain"]])
def test_missing_git_reports_cleanly(args, empty_bin):
    rc, output = _run_cli(args, _env(empty_bin))

    assert GIT_MISSING_MESSAGE in output, output
    assert "Traceback (most recent call last)" not in output, output
    assert rc == 1, output


# plain2code is the CLI entry point; file_utils is the module whose import first
# pulls in GitPython in the real startup chain. (plain_modules is not tested as a
# standalone import: it has a pre-existing circular import with file_utils that
# fails regardless of git, so it is never imported first in practice.)
@pytest.mark.parametrize("module", ["plain2code", "file_utils"])
def test_importing_client_without_git_does_not_raise(module, empty_bin):
    # The earlier fix raised at import time, so merely importing the client
    # crashed before main() ran. Importing must now succeed with git absent.
    result = subprocess.run(
        [sys.executable, "-c", f"import {module}"],
        cwd=REPO_ROOT,
        env=_env(empty_bin),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, f"import {module} crashed:\n{result.stdout}\n{result.stderr}"


def test_version_still_works_when_git_present():
    # The startup check must not block normal use when git is available.
    rc, output = _run_cli(["--version"], _env(empty_bin=None))

    assert rc == 0, output
    assert "codeplain version" in output, output
