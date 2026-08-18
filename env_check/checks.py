"""The registry of environment checks the client is able to execute.

Security note: a check plan comes from the server and is therefore untrusted
input. This module is the only place that turns a plan entry into an action, and
it never executes a string supplied by the plan. A plan selects a ``type`` from
``CHECK_TYPES`` and supplies arguments that are validated here before any of the
handlers run. Subprocesses are always spawned without a shell, from an argument
list, with a binary resolved through ``shutil.which``.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
from typing import Any, Callable, Optional
from urllib.parse import urlparse

from env_check.types import STATUS_FAILED, STATUS_PASSED

SUBPROCESS_TIMEOUT_SECONDS = 15
NETWORK_TIMEOUT_SECONDS = 8

# Version flags are the only arguments the preflight is ever allowed to pass to a
# binary it did not choose itself.
ALLOWED_VERSION_ARGS = ("--version", "-version", "version", "-v", "-V", "--v")

COMMAND_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.+-]*$")
PYTHON_MODULE_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$")
NODE_MODULE_PATTERN = re.compile(r"^(@[A-Za-z0-9][A-Za-z0-9._-]*/)?[A-Za-z0-9][A-Za-z0-9._-]*$")
HOST_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.\-_]*$")
DEFAULT_VERSION_PATTERN = re.compile(r"(\d+(?:\.\d+)*)")


class InvalidCheckArguments(Exception):
    """Raised when a plan entry carries arguments the client refuses to act on."""


# --------------------------------------------------------------------------- #
# Argument validators
# --------------------------------------------------------------------------- #


def _require_str(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InvalidCheckArguments(f"'{name}' must be a non-empty string")
    if "\x00" in value:
        raise InvalidCheckArguments(f"'{name}' must not contain null bytes")
    return value.strip()


def _validate_command(value: Any) -> str:
    command = _require_str(value, "command")
    if not COMMAND_PATTERN.match(command):
        raise InvalidCheckArguments(
            f"'{command}' is not a bare executable name; paths and shell metacharacters are not allowed"
        )
    return command


def _validate_version_arg(value: Any) -> str:
    version_arg = _require_str(value, "version_arg")
    if version_arg not in ALLOWED_VERSION_ARGS:
        raise InvalidCheckArguments(
            f"'{version_arg}' is not an allowed version flag (allowed: {', '.join(ALLOWED_VERSION_ARGS)})"
        )
    return version_arg


def _validate_regex(value: Any) -> str:
    pattern = _require_str(value, "pattern")
    try:
        re.compile(pattern)
    except re.error as error:
        raise InvalidCheckArguments(f"invalid regular expression: {error}")
    return pattern


def _validate_python_module(value: Any) -> str:
    module = _require_str(value, "module")
    if not PYTHON_MODULE_PATTERN.match(module):
        raise InvalidCheckArguments(f"'{module}' is not a valid Python module name")
    return module


def _validate_node_module(value: Any) -> str:
    module = _require_str(value, "module")
    if not NODE_MODULE_PATTERN.match(module):
        raise InvalidCheckArguments(f"'{module}' is not a valid Node package name")
    return module


def _validate_env_var_name(value: Any) -> str:
    name = _require_str(value, "name")
    if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", name):
        raise InvalidCheckArguments(f"'{name}' is not a valid environment variable name")
    return name


def _validate_path(value: Any) -> str:
    return os.path.expanduser(_require_str(value, "path"))


def _validate_port(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidCheckArguments("'port' must be an integer")
    if not 1 <= value <= 65535:
        raise InvalidCheckArguments("'port' must be between 1 and 65535")
    return value


def _validate_host(value: Any) -> str:
    host = _require_str(value, "host")
    if not HOST_PATTERN.match(host):
        raise InvalidCheckArguments(f"'{host}' is not a valid host name")
    return host


def _validate_url(value: Any) -> str:
    url = _require_str(value, "url")
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise InvalidCheckArguments("'url' must use the http or https scheme")
    if not parsed.netloc:
        raise InvalidCheckArguments("'url' must include a host")
    return url


def _validate_http_method(value: Any) -> str:
    method = _require_str(value, "method").upper()
    if method not in ("HEAD", "GET"):
        raise InvalidCheckArguments("'method' must be HEAD or GET")
    return method


def _validate_http_status(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidCheckArguments("'expected_status' must be an integer")
    if not 100 <= value <= 599:
        raise InvalidCheckArguments("'expected_status' must be a valid HTTP status code")
    return value


def _validate_bool(value: Any) -> bool:
    if not isinstance(value, bool):
        raise InvalidCheckArguments("expected a boolean")
    return value


# --------------------------------------------------------------------------- #
# Version helpers
# --------------------------------------------------------------------------- #


def parse_version(text: str) -> Optional[tuple[int, ...]]:
    """Extract the first dotted version number from ``text``."""
    match = DEFAULT_VERSION_PATTERN.search(text)
    if match is None:
        return None
    try:
        return tuple(int(part) for part in match.group(1).split("."))
    except ValueError:
        return None


def version_at_least(found: tuple[int, ...], minimum: tuple[int, ...]) -> bool:
    length = max(len(found), len(minimum))
    padded_found = found + (0,) * (length - len(found))
    padded_minimum = minimum + (0,) * (length - len(minimum))
    return padded_found >= padded_minimum


def _run(command: list[str], cwd: Optional[str] = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=SUBPROCESS_TIMEOUT_SECONDS,
        cwd=cwd,
        shell=False,
    )


# --------------------------------------------------------------------------- #
# Handlers
# --------------------------------------------------------------------------- #


def check_command_available(args: dict[str, Any]) -> tuple[str, str]:
    command = args["command"]
    resolved = shutil.which(command)
    if resolved is None:
        return STATUS_FAILED, f"'{command}' was not found on PATH"

    version_arg = args.get("version_arg")
    if version_arg is None:
        return STATUS_PASSED, f"found at {resolved}"

    try:
        completed = _run([resolved, version_arg])
    except (OSError, subprocess.SubprocessError) as error:
        return STATUS_PASSED, f"found at {resolved} (version probe failed: {error})"

    output = f"{completed.stdout}\n{completed.stderr}".strip()
    pattern = args.get("version_regex")
    if pattern:
        match = re.search(pattern, output)
        version_text = match.group(1) if match and match.groups() else (match.group(0) if match else "")
    else:
        version_text = output

    found = parse_version(version_text) if version_text else None
    minimum_text = args.get("min_version")

    if minimum_text is None:
        if found is None:
            return STATUS_PASSED, f"found at {resolved}"
        return STATUS_PASSED, f"found at {resolved} (version {'.'.join(str(part) for part in found)})"

    minimum = parse_version(minimum_text)
    if found is None or minimum is None:
        # Never fail on an unreadable version string -- that would block renders
        # on perfectly good toolchains that format their output unusually.
        return STATUS_PASSED, f"found at {resolved} (version could not be determined, wanted >= {minimum_text})"

    found_text = ".".join(str(part) for part in found)
    if version_at_least(found, minimum):
        return STATUS_PASSED, f"found at {resolved} (version {found_text} >= {minimum_text})"
    return STATUS_FAILED, f"version {found_text} at {resolved} is older than the required {minimum_text}"


def check_env_var_set(args: dict[str, Any]) -> tuple[str, str]:
    name = args["name"]
    if name not in os.environ:
        return STATUS_FAILED, f"{name} is not set"

    value = os.environ[name]
    if not value.strip() and not args.get("allow_empty", False):
        return STATUS_FAILED, f"{name} is set but empty"

    pattern = args.get("pattern")
    if pattern and not re.search(pattern, value):
        # The value itself is never reported -- these are frequently secrets.
        return STATUS_FAILED, f"{name} is set but does not match the expected format"

    return STATUS_PASSED, f"{name} is set ({len(value)} characters)"


def check_file_exists(args: dict[str, Any]) -> tuple[str, str]:
    path = args["path"]
    must_be_dir = args.get("must_be_dir", False)
    must_be_executable = args.get("must_be_executable", False)

    if not os.path.exists(path):
        return STATUS_FAILED, f"{path} does not exist"
    if must_be_dir and not os.path.isdir(path):
        return STATUS_FAILED, f"{path} exists but is not a directory"
    if not must_be_dir and os.path.isdir(path):
        return STATUS_FAILED, f"{path} is a directory, expected a file"
    if must_be_executable and not os.access(path, os.X_OK):
        return STATUS_FAILED, f"{path} exists but is not executable"

    return STATUS_PASSED, f"{path} exists"


def check_directory_writable(args: dict[str, Any]) -> tuple[str, str]:
    path = args["path"]
    # The directory itself may not exist yet (build folders are created on
    # demand), so the nearest existing ancestor is what has to be writable.
    probe = os.path.abspath(path)
    while probe and not os.path.isdir(probe):
        parent = os.path.dirname(probe)
        if parent == probe:
            break
        probe = parent

    if not os.path.isdir(probe):
        return STATUS_FAILED, f"no existing directory found for {path}"
    if not os.access(probe, os.W_OK):
        return STATUS_FAILED, f"{probe} is not writable"
    return STATUS_PASSED, f"{probe} is writable"


def check_python_module_importable(args: dict[str, Any]) -> tuple[str, str]:
    module = args["module"]
    interpreter = args.get("interpreter", "python3")
    resolved = shutil.which(interpreter)
    if resolved is None:
        return STATUS_FAILED, f"interpreter '{interpreter}' was not found on PATH"

    try:
        completed = _run([resolved, "-c", f"import {module}"])
    except (OSError, subprocess.SubprocessError) as error:
        return STATUS_FAILED, f"could not run {interpreter}: {error}"

    if completed.returncode == 0:
        return STATUS_PASSED, f"{module} is importable by {resolved}"
    return STATUS_FAILED, f"{module} is not importable by {resolved}"


def check_node_module_resolvable(args: dict[str, Any]) -> tuple[str, str]:
    module = args["module"]
    resolved = shutil.which("node")
    if resolved is None:
        return STATUS_FAILED, "'node' was not found on PATH"

    cwd = args.get("cwd")
    if cwd and not os.path.isdir(cwd):
        cwd = None

    try:
        completed = _run([resolved, "-e", f"require.resolve({json.dumps(module)})"], cwd=cwd)
    except (OSError, subprocess.SubprocessError) as error:
        return STATUS_FAILED, f"could not run node: {error}"

    if completed.returncode == 0:
        return STATUS_PASSED, f"{module} resolves for node"
    return STATUS_FAILED, f"{module} does not resolve for node"


def check_tcp_port_free(args: dict[str, Any]) -> tuple[str, str]:
    port = args["port"]
    host = args.get("host", "127.0.0.1")
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind((host, port))
        except OSError:
            return STATUS_FAILED, f"port {port} on {host} is already in use"
    return STATUS_PASSED, f"port {port} on {host} is free"


def check_tcp_service_reachable(args: dict[str, Any]) -> tuple[str, str]:
    host = args["host"]
    port = args["port"]
    try:
        with socket.create_connection((host, port), timeout=NETWORK_TIMEOUT_SECONDS):
            return STATUS_PASSED, f"{host}:{port} accepted a connection"
    except OSError as error:
        return STATUS_FAILED, f"{host}:{port} is not reachable ({error})"


def check_http_reachable(args: dict[str, Any]) -> tuple[str, str]:
    import requests

    url = args["url"]
    method = args.get("method", "HEAD")
    expected_status = args.get("expected_status")

    try:
        response = requests.request(method, url, timeout=NETWORK_TIMEOUT_SECONDS, allow_redirects=True)
    except requests.RequestException as error:
        return STATUS_FAILED, f"{url} is not reachable ({error})"

    if expected_status is not None and response.status_code != expected_status:
        return STATUS_FAILED, f"{url} responded with {response.status_code}, expected {expected_status}"
    if expected_status is None and response.status_code >= 500:
        return STATUS_FAILED, f"{url} responded with {response.status_code}"
    return STATUS_PASSED, f"{url} responded with {response.status_code}"


# --------------------------------------------------------------------------- #
# The registry
# --------------------------------------------------------------------------- #

CheckHandler = Callable[[dict[str, Any]], tuple[str, str]]


class CheckType:
    def __init__(
        self,
        handler: CheckHandler,
        required: dict[str, Callable[[Any], Any]],
        optional: Optional[dict[str, Callable[[Any], Any]]] = None,
    ):
        self.handler = handler
        self.required = required
        self.optional = optional or {}

    def validate(self, args: Any) -> dict[str, Any]:
        if args is None:
            args = {}
        if not isinstance(args, dict):
            raise InvalidCheckArguments("'args' must be an object")

        validated: dict[str, Any] = {}
        for name, validator in self.required.items():
            if name not in args:
                raise InvalidCheckArguments(f"missing required argument '{name}'")
            validated[name] = validator(args[name])

        for name, validator in self.optional.items():
            if name in args and args[name] is not None:
                validated[name] = validator(args[name])

        return validated


CHECK_TYPES: dict[str, CheckType] = {
    "command_available": CheckType(
        check_command_available,
        required={"command": _validate_command},
        optional={
            "version_arg": _validate_version_arg,
            "version_regex": _validate_regex,
            "min_version": lambda value: _require_str(value, "min_version"),
        },
    ),
    "env_var_set": CheckType(
        check_env_var_set,
        required={"name": _validate_env_var_name},
        optional={"allow_empty": _validate_bool, "pattern": _validate_regex},
    ),
    "file_exists": CheckType(
        check_file_exists,
        required={"path": _validate_path},
        optional={"must_be_dir": _validate_bool, "must_be_executable": _validate_bool},
    ),
    "directory_writable": CheckType(
        check_directory_writable,
        required={"path": _validate_path},
    ),
    "python_module_importable": CheckType(
        check_python_module_importable,
        required={"module": _validate_python_module},
        optional={"interpreter": _validate_command},
    ),
    "node_module_resolvable": CheckType(
        check_node_module_resolvable,
        required={"module": _validate_node_module},
        optional={"cwd": _validate_path},
    ),
    "tcp_port_free": CheckType(
        check_tcp_port_free,
        required={"port": _validate_port},
        optional={"host": _validate_host},
    ),
    "tcp_service_reachable": CheckType(
        check_tcp_service_reachable,
        required={"host": _validate_host, "port": _validate_port},
    ),
    "http_reachable": CheckType(
        check_http_reachable,
        required={"url": _validate_url},
        optional={"method": _validate_http_method, "expected_status": _validate_http_status},
    ),
}

SUPPORTED_CHECK_TYPES = tuple(sorted(CHECK_TYPES))
