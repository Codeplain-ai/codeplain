"""Assembly of the context the server needs to plan environment checks."""

from __future__ import annotations

import os
import platform
import sys
from typing import Any, Optional

import file_utils
from plain2code_console import console
from plain_modules import PlainModule

# The planner only needs to see enough of a resource to recognise what the
# project talks to. Whole API reference dumps would dominate the payload.
MAX_RESOURCE_CHARS = 20000
MAX_TOTAL_RESOURCE_CHARS = 200000
MAX_SCRIPT_CHARS = 40000

TRUNCATION_NOTICE = "\n\n... [truncated by the environment preflight] ..."

SCRIPT_ARGUMENTS = (
    "unittests_script",
    "conformance_tests_script",
    "prepare_environment_script",
)


def _truncate(content: str, limit: int) -> str:
    if len(content) <= limit:
        return content
    return content[:limit] + TRUNCATION_NOTICE


def _collect_modules(plain_module: PlainModule) -> list[dict[str, Any]]:
    modules = []
    for module in plain_module.all_required_modules + [plain_module]:
        modules.append({"module_name": module.module_name, "plain_source_tree": module.plain_source})
    return modules


def _collect_linked_resources(plain_module: PlainModule) -> dict[str, str]:
    """Load every linked resource across the whole module tree, within a size budget."""
    resources: dict[str, str] = {}
    total_chars = 0

    for module in plain_module.all_required_modules + [plain_module]:
        try:
            module_resources = file_utils.load_linked_resources(
                module.template_dirs, module.resources_list, module.module_name
            )
        except Exception as error:
            # A resource that cannot be loaded is the render's problem to report,
            # not the preflight's -- it must not turn into a preflight failure.
            console.debug(f"Environment preflight could not load resources for {module.module_name}: {error}")
            continue

        for file_name, content in module_resources.items():
            if file_name in resources or not isinstance(content, str):
                continue
            if total_chars >= MAX_TOTAL_RESOURCE_CHARS:
                break

            truncated = _truncate(content, MAX_RESOURCE_CHARS)
            resources[file_name] = truncated
            total_chars += len(truncated)

    return resources


def _read_script(path: Optional[str]) -> Optional[dict[str, str]]:
    if not path:
        return None

    try:
        with open(path, "r", encoding="utf-8", errors="replace") as script_file:
            content = script_file.read()
    except OSError as error:
        console.debug(f"Environment preflight could not read {path}: {error}")
        return {"path": path, "content": ""}

    return {"path": path, "content": _truncate(content, MAX_SCRIPT_CHARS)}


def _collect_test_scripts(args) -> dict[str, dict[str, str]]:
    scripts = {}
    for argument_name in SCRIPT_ARGUMENTS:
        script = _read_script(getattr(args, argument_name, None))
        if script is not None:
            scripts[argument_name] = script
    return scripts


def collect_host_info() -> dict[str, str]:
    """Describe the machine so the planner can emit checks and hints that fit it."""
    return {
        "platform": sys.platform,
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "python_version": platform.python_version(),
        "shell": os.path.basename(os.environ.get("SHELL", "")) or "unknown",
    }


def build_environment_context(plain_module: PlainModule, args) -> dict[str, Any]:
    """Build the payload for the ``/check_environment`` endpoint."""
    return {
        "modules": _collect_modules(plain_module),
        "linked_resources": _collect_linked_resources(plain_module),
        "test_scripts": _collect_test_scripts(args),
        "host_info": collect_host_info(),
    }
