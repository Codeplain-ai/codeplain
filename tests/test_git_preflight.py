"""Regression tests for issue #133.

When git is unusable the CLI must report a clean, one-line diagnosis and exit
non-zero -- never dump a Python traceback.

The failure is import-time: GitPython probes for the git executable when it is
first imported and raises from module scope, before ``main()`` runs. A check
inside ``main()`` cannot intercept it, so these tests run the CLI in a
subprocess and assert on what a user would actually see. Two failure modes are
covered, because they surface through different GitPython paths:

- git missing entirely -- nothing named ``git`` on PATH.
- git present but broken -- a ``git`` that resolves but exits non-zero, as on
  macOS without the Command Line Tools. ``GIT_PYTHON_REFRESH=quiet`` does not
  suppress this one, which is why a PATH lookup alone is not a sufficient check.

``sys.executable`` is launched by absolute path, so replacing PATH does not stop
Python from starting -- it only controls which git, if any, can be found.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from git_preflight import (
    GIT_BROKEN_MESSAGE,
    GIT_MISSING_MESSAGE,
    MACOS_BROKEN_GIT_HINT,
    broken_git_message,
    git_runs,
)

REPO_ROOT = Path(__file__).resolve().parents[1]

TRACEBACK_MARKER = "Traceback (most recent call last)"

# Every entry point, including the ones that do no git work themselves: the crash
# was in the import chain, so it hit them all equally.
CLI_INVOCATIONS = [
    pytest.param(["--version"], id="version"),
    pytest.param(["--status"], id="status"),
    pytest.param(["does-not-exist.plain"], id="render"),
]


def _env(path_dir=None):
    """The current environment, with PATH optionally replaced.

    GitPython overrides are dropped so the PATH search is the only thing
    deciding whether git is found.
    """
    env = os.environ.copy()
    env.pop("GIT_PYTHON_GIT_EXECUTABLE", None)
    env.pop("GIT_PYTHON_REFRESH", None)
    if path_dir is not None:
        env["PATH"] = str(path_dir)
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


def _normalize(output):
    """Collapse whitespace so assertions survive console line wrapping."""
    return " ".join(output.split())


@pytest.fixture
def no_git_dir(tmp_path):
    """A directory containing no git at all, used as the whole PATH."""
    d = tmp_path / "no-git"
    d.mkdir()
    return d


@pytest.fixture
def broken_git_dir(tmp_path):
    """A directory whose git resolves but always fails, used as the whole PATH.

    This is the shape of macOS without the Command Line Tools: the name is
    there, running it is not possible.
    """
    d = tmp_path / "broken-git"
    d.mkdir()
    if sys.platform == "win32":
        git = d / "git.bat"
        git.write_text("@echo off\nexit /b 127\n")
    else:
        git = d / "git"
        git.write_text("#!/bin/sh\nexit 127\n")
        git.chmod(0o755)
    return d


@pytest.mark.parametrize("args", CLI_INVOCATIONS)
def test_missing_git_reports_cleanly(args, no_git_dir):
    rc, output = _run_cli(args, _env(no_git_dir))

    assert GIT_MISSING_MESSAGE in _normalize(output), output
    assert TRACEBACK_MARKER not in output, output
    assert rc == 1, output


@pytest.mark.parametrize("args", CLI_INVOCATIONS)
def test_broken_git_reports_cleanly(args, broken_git_dir):
    # A git that resolves but cannot run must be diagnosed as broken rather than
    # missing -- and must not reach GitPython's import-time traceback.
    rc, output = _run_cli(args, _env(broken_git_dir))
    normalized = _normalize(output)

    assert GIT_BROKEN_MESSAGE in normalized, output
    assert GIT_MISSING_MESSAGE not in normalized, output
    assert TRACEBACK_MARKER not in output, output
    assert rc == 1, output


@pytest.mark.parametrize("args", CLI_INVOCATIONS)
def test_missing_git_does_not_leak_gitpython_advice(args, no_git_dir):
    # GitPython's own message coaches the user about $GIT_PYTHON_REFRESH and
    # git.refresh(), which are irrelevant to someone who just needs to install
    # git. None of it should reach the user.
    _, output = _run_cli(args, _env(no_git_dir))

    assert "GIT_PYTHON_REFRESH" not in output, output
    assert "Bad git executable" not in output, output


# plain_modules is deliberately not tested as a standalone import: it has a
# pre-existing circular import with file_utils that fails whether or not git is
# present, so the assertion would hold for the wrong reason.
@pytest.mark.parametrize("module", ["plain2code", "file_utils"])
@pytest.mark.parametrize("path_fixture", ["no_git_dir", "broken_git_dir"])
def test_importing_git_dependent_module_exits_cleanly(module, path_fixture, request):
    """Importing a module in the CLI's git-dependent import chain must diagnose
    git rather than traceback."""
    path_dir = request.getfixturevalue(path_fixture)
    result = subprocess.run(
        [sys.executable, "-c", f"import {module}"],
        cwd=REPO_ROOT,
        env=_env(path_dir),
        capture_output=True,
        text=True,
        timeout=120,
    )
    output = result.stdout + result.stderr

    assert TRACEBACK_MARKER not in output, output
    assert result.returncode == 1, output


def test_cli_works_when_git_is_present():
    # The check must not get in the way of normal use.
    rc, output = _run_cli(["--version"], _env())

    assert rc == 0, output
    assert "codeplain version" in output, output


def test_git_runs_accepts_real_git():
    import shutil

    git_path = shutil.which("git")
    assert git_path is not None, "test environment has no git"
    assert git_runs(git_path) is True


def test_git_runs_rejects_failing_git(broken_git_dir):
    broken = broken_git_dir / ("git.bat" if sys.platform == "win32" else "git")

    assert git_runs(str(broken)) is False


def test_git_runs_rejects_nonexistent_path(tmp_path):
    # OSError from exec must be treated as unusable, not propagated.
    assert git_runs(str(tmp_path / "definitely-not-here")) is False


@pytest.mark.skipif(sys.platform != "darwin", reason="hint is macOS-specific")
def test_broken_git_message_hints_at_command_line_tools_on_macos():
    assert MACOS_BROKEN_GIT_HINT in broken_git_message()


@pytest.mark.skipif(sys.platform == "darwin", reason="hint is macOS-specific")
def test_broken_git_message_has_no_macos_hint_elsewhere():
    assert broken_git_message() == GIT_BROKEN_MESSAGE
