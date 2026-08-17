"""The client-side final audit of the platform-test runtime's portability boundary.

The API discards responses that leak `codeplain-tty` into delivered code, so by the
time a module render completes its build folder should be clean. This audit is the last
net before the build is published or copied: it walks the implementation tree and fails
the render on any reference to the helper or its environment prefix, because a delivered
application must run in a clean environment where none of Codeplain's test tooling
exists.

Internal conformance and acceptance tests live outside the build folder (in the
module's tests tree), so nothing here needs an allowlist: any hit inside the build
folder is a violation.
"""

import os
from typing import List

# The executable name, the module name, and the environment prefix — the same markers
# the API's response validation uses.
HELPER_REFERENCE_MARKERS = ("codeplain-tty", "codeplain_tty", "CODEPLAIN_TTY_")

# Directories that carry no delivered source and may be large.
SKIPPED_DIRECTORIES = {".git", ".venv", "node_modules", "__pycache__", ".tmp", "dist", "build", "target"}

MAX_AUDITED_FILE_BYTES = 4 * 1024 * 1024  # a delivered source file larger than this is not source


class PlatformBoundaryViolation(Exception):
    """A delivered build references Codeplain's private test tooling."""


def find_platform_references(build_folder: str) -> List[str]:
    """Build-folder-relative paths of files referencing the platform test helper."""
    violations = []
    for root, directories, file_names in os.walk(build_folder):
        directories[:] = [name for name in directories if name not in SKIPPED_DIRECTORIES]
        for file_name in file_names:
            path = os.path.join(root, file_name)
            relative = os.path.relpath(path, build_folder)
            if any(marker in file_name for marker in HELPER_REFERENCE_MARKERS):
                violations.append(relative)
                continue
            try:
                if os.path.getsize(path) > MAX_AUDITED_FILE_BYTES:
                    continue
                with open(path, "r", encoding="utf-8", errors="ignore") as source:
                    content = source.read()
            except OSError:
                continue  # unreadable files cannot ship a reference the target could read
            if any(marker in content for marker in HELPER_REFERENCE_MARKERS):
                violations.append(relative)
    return sorted(violations)


def audit_build_folder(build_folder: str) -> None:
    """Raises when the delivered build references the platform test runtime."""
    violations = find_platform_references(build_folder)
    if violations:
        raise PlatformBoundaryViolation(
            "The generated build references Codeplain's private test runtime and cannot be published. "
            f"Offending files: {', '.join(violations)}. "
            "The implementation must not depend on codeplain-tty or CODEPLAIN_TTY_* in any way."
        )
