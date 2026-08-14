"""Verify a working git is available before GitPython is imported.

GitPython probes for the git executable when it is first imported and raises from
module scope if it is missing or broken. That happens while plain2code is still
importing its own modules, so a check inside main() cannot intercept it -- the
user gets a raw traceback instead of an explanation.

Two failure modes need separate handling. A missing git is simply absent from
PATH. A broken git resolves but cannot run: on macOS without the Command Line
Tools /usr/bin/git is a stub that exits non-zero, and stale shims behave the same
way. GIT_PYTHON_REFRESH=quiet does not help with the latter, because GitPython
only consults that setting for a git it cannot find. A PATH lookup alone is
therefore not sufficient, so this module runs ``git version`` -- mirroring the
``git_available`` check in install/bash/install.sh.
"""

from __future__ import annotations

import shutil
import subprocess
import sys

from plain2code_console import console

# Long enough for a cold-cache process spawn, short enough that a wedged shim
# cannot hang the CLI indefinitely.
GIT_VERSION_TIMEOUT_SECONDS = 10

GIT_MISSING_MESSAGE = "git is not installed. Please install git and try again."
GIT_BROKEN_MESSAGE = "git is installed but not working. Please repair your git installation and try again."
MACOS_BROKEN_GIT_HINT = (
    "This usually means the Command Line Tools are missing; install them with 'xcode-select --install'."
)


def broken_git_message() -> str:
    """The broken-git message, with a platform hint where there is a likely cause."""
    if sys.platform == "darwin":
        return f"{GIT_BROKEN_MESSAGE}\n{MACOS_BROKEN_GIT_HINT}"

    return GIT_BROKEN_MESSAGE


def find_git() -> str | None:
    """Return the path to the git executable, or None if it is not on PATH."""
    return shutil.which("git")


def git_runs(git_path: str) -> bool:
    """Whether ``git version`` actually succeeds for the given executable.

    A resolvable name can still be unusable, so the command is run rather than
    assumed. Any failure to execute it at all counts as unusable.
    """
    try:
        result = subprocess.run(
            [git_path, "version"],
            capture_output=True,
            timeout=GIT_VERSION_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError):
        return False

    return result.returncode == 0


def require_git() -> None:
    """Exit with a clean message unless a working git is available."""
    git_path = find_git()

    if git_path is None:
        console.error(f"{GIT_MISSING_MESSAGE}\n")
        sys.exit(1)

    if not git_runs(git_path):
        console.error(f"{broken_git_message()}\n")
        sys.exit(1)
