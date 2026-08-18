"""The client-side final audit of the platform-test runtime's portability boundary.

The API discards responses that leak `codeplain-tty` into delivered code, so by the
time a module render completes its build folder should be clean. This audit is the last
net before the build is published or copied: it walks the implementation tree and fails
the render on any reference to the helper or its environment prefix, because a delivered
application must run in a clean environment where none of Codeplain's test tooling
exists.

Internal conformance and acceptance tests are exempt. They are what the helper exists
for, and they do turn up inside the audited tree — a module's build folder can carry a
`conformance_tests/` subtree, and benchmark renders showed the audit failing them.
Auditing those is not a stricter boundary, it is a false one: it aborts a successful
render over test code that is never delivered.
"""

import os
from typing import List

# The executable name, the module name, and the environment prefix — the same markers
# the API's response validation uses.
HELPER_REFERENCE_MARKERS = ("codeplain-tty", "plain2code_tty", "CODEPLAIN_TTY_")

# Directories that carry no delivered source and may be large.
SKIPPED_DIRECTORIES = {".git", ".venv", "node_modules", "__pycache__", ".tmp", "dist", "build", "target"}

# Internal test trees, which are allowed to drive the helper and are never delivered.
# Named separately from the above because skipping them is a boundary decision, not a
# performance one.
INTERNAL_TEST_DIRECTORIES = {"conformance_tests", "acceptance_tests", "dist_conformance_tests"}

MAX_AUDITED_FILE_BYTES = 4 * 1024 * 1024  # a delivered source file larger than this is not source

# Suffixes that are never delivered source. Logs matter most: the renderer's own
# codeplain.log records broker activity and module names, so auditing it reports the
# render's diagnostics as if the application had referenced the helper.
SKIPPED_FILE_SUFFIXES = (".log",)


class PlatformBoundaryViolation(Exception):
    """A delivered build references Codeplain's private test tooling."""


def find_platform_references(build_folder: str) -> List[str]:
    """Build-folder-relative paths of files referencing the platform test helper."""
    violations = []
    for root, directories, file_names in os.walk(build_folder):
        directories[:] = [
            name for name in directories if name not in SKIPPED_DIRECTORIES and name not in INTERNAL_TEST_DIRECTORIES
        ]
        for file_name in file_names:
            if file_name.endswith(SKIPPED_FILE_SUFFIXES):
                continue
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
