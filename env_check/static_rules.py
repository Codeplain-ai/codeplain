"""The static checks: the fixed set the client derives on its own, without the server.

These cover the failure modes that have nothing to do with what the specs say --
a script that is not executable, a missing toolchain the renderer itself needs,
a build folder that cannot be written. They run even when the dynamic checks are
unavailable, and every one of them carries ORIGIN_STATIC.
"""

from __future__ import annotations

import os
import sys

from env_check.types import ORIGIN_STATIC, SEVERITY_ERROR, Advisory, CheckSpec

POWERSHELL_SUFFIX = ".ps1"

SCRIPT_ARGUMENTS = (
    ("unittests_script", "unit tests script"),
    ("conformance_tests_script", "conformance tests script"),
    ("prepare_environment_script", "prepare environment script"),
)


def _script_paths(args) -> list[tuple[str, str, str]]:
    """Return (argument name, human label, path) for every configured script."""
    configured = []
    for argument_name, label in SCRIPT_ARGUMENTS:
        path = getattr(args, argument_name, None)
        if path:
            configured.append((argument_name, label, path))
    return configured


def _script_checks(args) -> list[CheckSpec]:
    checks: list[CheckSpec] = []
    on_windows = sys.platform == "win32"

    for argument_name, label, path in _script_paths(args):
        checks.append(
            CheckSpec(
                id=f"static-{argument_name}-exists",
                type="file_exists",
                severity=SEVERITY_ERROR,
                description=f"The {label} exists and is executable",
                args={"path": path, "must_be_executable": not on_windows},
                reason=f"The renderer executes {path} after every functionality it implements.",
                remediation={"default": f"chmod +x {path}"} if not on_windows else {},
                origin=ORIGIN_STATIC,
            )
        )

    return checks


def _script_advisories(args) -> list[Advisory]:
    """Flag scripts whose flavour does not match the platform they will run on."""
    advisories: list[Advisory] = []
    on_windows = sys.platform == "win32"

    for _, label, path in _script_paths(args):
        is_powershell = path.lower().endswith(POWERSHELL_SUFFIX)

        if on_windows and not is_powershell:
            advisories.append(
                Advisory(
                    id=f"static-{os.path.basename(path)}-not-powershell",
                    severity=SEVERITY_ERROR,
                    title=f"The {label} is not a PowerShell script",
                    detail=(
                        f"{path} will be executed on Windows, where only PowerShell (.ps1) scripts are supported. "
                        "The renderer refuses to run it."
                    ),
                    remediation=f"Provide a PowerShell version of the {label} and point the configuration at it.",
                    origin=ORIGIN_STATIC,
                )
            )
        elif not on_windows and is_powershell:
            advisories.append(
                Advisory(
                    id=f"static-{os.path.basename(path)}-powershell-on-posix",
                    severity=SEVERITY_ERROR,
                    title=f"The {label} is a PowerShell script",
                    detail=(
                        f"{path} will be executed directly on {sys.platform}, which cannot run a .ps1 file. "
                        "Provide the shell version of the script instead."
                    ),
                    remediation=f"Point the configuration at the shell (.sh) version of the {label}.",
                    origin=ORIGIN_STATIC,
                )
            )

    return advisories


def _toolchain_checks() -> list[CheckSpec]:
    checks = [
        CheckSpec(
            id="static-git",
            type="command_available",
            severity=SEVERITY_ERROR,
            description="git is installed",
            args={"command": "git", "version_arg": "--version"},
            reason="Every implemented functionality is committed to a git repository in the build folder.",
            remediation={
                "darwin": "brew install git",
                "linux": "sudo apt-get install git",
                "win32": "winget install Git.Git",
            },
            origin=ORIGIN_STATIC,
        )
    ]

    if sys.platform != "win32":
        checks.append(
            CheckSpec(
                id="static-bash",
                type="command_available",
                severity=SEVERITY_ERROR,
                description="bash is installed",
                args={"command": "bash", "version_arg": "--version"},
                reason="The testing scripts are shell scripts executed by the renderer.",
                remediation={
                    "darwin": "bash ships with macOS; check that /bin/bash is on PATH",
                    "linux": "sudo apt-get install bash",
                },
                origin=ORIGIN_STATIC,
            )
        )

    return checks


def _build_folder_checks(args) -> list[CheckSpec]:
    build_folder = getattr(args, "build_folder", None)
    if not build_folder:
        return []

    return [
        CheckSpec(
            id="static-build-folder-writable",
            type="directory_writable",
            severity=SEVERITY_ERROR,
            description="The build folder is writable",
            args={"path": build_folder},
            reason="Generated code, unit tests and conformance tests are all written into the build folder.",
            remediation={"default": f"Make sure {build_folder} (or its parent) is writable."},
            origin=ORIGIN_STATIC,
        )
    ]


def build_static_plan(args) -> tuple[list[CheckSpec], list[Advisory]]:
    """Return the checks and advisories the client derives without the server."""
    checks = _toolchain_checks() + _script_checks(args) + _build_folder_checks(args)
    return checks, _script_advisories(args)
