"""Verify a working git is available before GitPython is imported.

GitPython probes for the git executable on import and raises from module scope
if it is missing or broken, so the CLI would crash with a traceback before
main() could explain it.
"""

import shutil
import subprocess
import sys

from plain2code_console import console

GIT_MISSING_MESSAGE = "git is not installed. Please install git and try again."
GIT_BROKEN_MESSAGE = "git is installed but not working. Please repair your git installation and try again."
MACOS_BROKEN_GIT_HINT = (
    "This usually means the Command Line Tools are missing; install them with 'xcode-select --install'."
)


def git_runs(git_path):
    """Whether `git version` succeeds. A git on PATH can still be unusable: on
    macOS without the Command Line Tools /usr/bin/git is a stub that exits with
    an xcrun error, and GIT_PYTHON_REFRESH=quiet does not suppress that."""
    try:
        return subprocess.run([git_path, "version"], capture_output=True, timeout=10).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def require_git():
    """Exit with a clean message unless a working git is available."""
    git_path = shutil.which("git")
    if git_path is None:
        console.error(f"{GIT_MISSING_MESSAGE}\n")
        sys.exit(1)

    if not git_runs(git_path):
        hint = f"\n{MACOS_BROKEN_GIT_HINT}" if sys.platform == "darwin" else ""
        console.error(f"{GIT_BROKEN_MESSAGE}{hint}\n")
        sys.exit(1)
