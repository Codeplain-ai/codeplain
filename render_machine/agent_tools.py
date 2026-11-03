"""Client-side implementations of the tools a server-side agent can call.

The server declares the tools to the LLM (codeplain-api: src/agent/tools.py) and forwards
the model's calls; this module executes them against the local build folder and returns
plain-text results. Relative paths resolve against the build folder. Each agent works in an
AgentWorkspace that says what it may read (by default the build folder and the full test logs it
was pointed to), what it may write (by default the build folder) and which tools it may use. A tool
given a RenderContext instead works in the unit-test fixing agent's workspace.
"""

import fnmatch
import glob
import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Union

from plain2code_console import console
from render_machine import render_utils
from render_machine.render_context import RenderContext

DEFAULT_READ_LIMIT = 200
MAX_LINE_CHARS = 10_000
MAX_OUTPUT_CHARS = 30_000
EDIT_SNIPPET_CONTEXT_LINES = 3
MAX_EDIT_SNIPPET_LINES = 60
MAX_GREP_CONTEXT_LINES = 20
# Tools without side effects; repeating one while no file changed returns a pointer instead.
READ_ONLY_TOOLS = ("read_file", "grep", "ls_files")
GREP_EXCLUDED_DIRS = (".git", "__pycache__", "node_modules", ".venv", "target", "dist", "build")


def _noop(_full_path: str) -> None:
    return None


@dataclass
class AgentWorkspace:
    """Where an agent's tool calls operate and what they may touch."""

    # Relative tool paths resolve against it, and grep reports matches inside it relative to it.
    build_folder: str
    writable_folders: list[str]
    # Readable besides the writable folders.
    readable_folders: list[str] = field(default_factory=list)
    readable_log_paths: set[str] = field(default_factory=set)
    # Results of read-only tool calls, keyed by call; cleared whenever a file changes.
    tool_result_cache: dict[str, str] = field(default_factory=dict)
    # Called with the absolute path of a file right before and right after a tool changes it.
    before_change: Callable[[str], None] = _noop
    after_change: Callable[[str], None] = _noop
    # The tools the agent may call; None allows every tool in TOOLS.
    allowed_tools: Optional[frozenset[str]] = None
    # Needed by run_unit_tests only.
    render_context: Any = None

    def __post_init__(self):
        self.build_folder = _absolute(self.build_folder)
        self.writable_folders = [_absolute(folder) for folder in self.writable_folders]
        self.readable_folders = [_absolute(folder) for folder in self.readable_folders]

    def describe_writable(self) -> str:
        return ", ".join(self.writable_folders) if self.writable_folders else "nothing"


def _absolute(path: str) -> str:
    return os.path.normpath(os.path.abspath(path))


def unit_tests_workspace(render_context: RenderContext) -> AgentWorkspace:
    """The unit-test fixing agent's workspace: the build folder, its test logs, and its loop's change tracking."""
    context = render_context.unit_tests_running_context
    return AgentWorkspace(
        build_folder=render_context.build_folder,
        writable_folders=[render_context.build_folder],
        readable_log_paths=render_context.unit_tests_agent_session.readable_log_paths,
        tool_result_cache=context.tool_result_cache,
        after_change=lambda full_path: _track_change(full_path, render_context),
        render_context=render_context,
    )


Target = Union[AgentWorkspace, RenderContext]


def _workspace(target: Target) -> AgentWorkspace:
    return target if isinstance(target, AgentWorkspace) else unit_tests_workspace(target)


def _build_folder(workspace: AgentWorkspace) -> str:
    return workspace.build_folder


def _resolve(file_path: str, workspace: AgentWorkspace) -> str:
    if os.path.isabs(file_path):
        return os.path.normpath(file_path)
    return os.path.normpath(os.path.join(workspace.build_folder, file_path))


def _within(path: str, folder: str) -> bool:
    return path == folder or path.startswith(folder + os.sep)


def _readable(path: str, workspace: AgentWorkspace) -> bool:
    folders = [workspace.build_folder, *workspace.writable_folders, *workspace.readable_folders]
    return any(_within(path, folder) for folder in folders) or path in workspace.readable_log_paths


def register_log_path(log_path: str, scope: Target) -> None:
    """Allow read_file/grep on a full test log the agent is pointed to."""
    _workspace(scope).readable_log_paths.add(_absolute(log_path))


def _writable(path: str, workspace: AgentWorkspace) -> bool:
    return any(_within(path, folder) for folder in workspace.writable_folders)


def _write_denied(full_path: str, workspace: AgentWorkspace) -> str:
    return f"Error: write access denied for '{full_path}' (writable: {workspace.describe_writable()})."


def _changed(full_path: str, workspace: AgentWorkspace) -> None:
    workspace.tool_result_cache.clear()
    workspace.after_change(full_path)


def _bound(text: str) -> str:
    """Cap very long lines and the total size so one tool result cannot flood the context."""
    lines = [
        line if len(line) <= MAX_LINE_CHARS else line[:MAX_LINE_CHARS] + "... [line truncated]"
        for line in text.split("\n")
    ]
    text = "\n".join(lines)
    if len(text) > MAX_OUTPUT_CHARS:
        head, tail = int(MAX_OUTPUT_CHARS * 0.6), int(MAX_OUTPUT_CHARS * 0.4)
        text = text[:head] + f"\n\n... [truncated {len(text) - head - tail:,} chars] ...\n\n" + text[-tail:]
    return text


def _track_change(full_path: str, render_context: RenderContext) -> None:
    context = render_context.unit_tests_running_context
    context.changed_files.add(os.path.relpath(full_path, _absolute(render_context.build_folder)))
    context.verified_passing, context.verified_passing_log_path = False, None
    context.tool_result_cache.clear()


def read_file(args: dict, scope: Target) -> str:
    workspace = _workspace(scope)
    full_path = _resolve(args.get("file_path", ""), workspace)
    if not _readable(full_path, workspace):
        return f"Error: read access denied for '{full_path}' (readable: the project folders and test logs)."
    if not os.path.isfile(full_path):
        return f"Error: file not found: '{full_path}'."
    with open(full_path, "r", encoding="utf-8", errors="replace") as f:
        lines = f.read().split("\n")
    offset = max(int(args.get("offset") or 1), 1)
    limit = int(args.get("limit") or DEFAULT_READ_LIMIT)
    selected = lines[offset - 1 : offset - 1 + limit]
    if not selected:
        return f"Error: offset {offset} is past the end of the file ({len(lines)} lines)."
    numbered = "\n".join(f"{offset + i}: {line}" for i, line in enumerate(selected))
    last = offset - 1 + len(selected)
    note = (
        f"\n[showing lines {offset}-{last} of {len(lines)}; use offset={last + 1} to continue]"
        if last < len(lines)
        else ""
    )
    return _bound(numbered) + note


def grep(args: dict, scope: Target) -> str:
    """Search a file, or the files under a directory, for a Python regular expression. Matching lines come back
    as `path:line:text`, context lines as `path-line-text`, with `--` between non-adjacent groups."""
    workspace = _workspace(scope)
    pattern = args.get("pattern", "")
    if not pattern:
        return "Error: pattern is required."
    try:
        regex = re.compile(pattern)
    except re.error as e:
        return (
            f"Error: invalid regular expression '{pattern}': {e}. The pattern uses Python regex syntax; "
            "escape special characters such as ( [ . * + ? with a backslash to match them literally."
        )
    target = _resolve(args.get("file_path") or ".", workspace)
    if not _readable(target, workspace):
        return f"Error: read access denied for '{target}'."
    if not os.path.exists(target):
        return f"Error: path not found: '{target}'."
    context_lines = min(max(int(args.get("context_lines") or 0), 0), MAX_GREP_CONTEXT_LINES)
    # Paths inside the build folder come back build-relative, the form the other tools accept; a test log
    # outside it keeps its absolute path.
    build_folder = _build_folder(workspace)
    output: list[str] = []
    for path in _grep_files(target, args.get("include") or ""):
        display = os.path.relpath(path, build_folder) if _within(path, build_folder) else path
        output.extend(_grep_file(path, display, regex, context_lines, output_has_groups=bool(output)))
        if sum(len(line) + 1 for line in output) > MAX_OUTPUT_CHARS:
            break
    if not output:
        return f"No matches for '{pattern}' in '{target}'."
    return _bound("\n".join(output))


def _grep_files(target: str, include: str):
    """The text files grep searches: `target` itself, or the files under it (skipping GREP_EXCLUDED_DIRS) whose
    name matches the `include` glob."""
    if os.path.isfile(target):
        yield target
        return
    for root, dirs, files in os.walk(target):
        dirs[:] = sorted(d for d in dirs if d not in GREP_EXCLUDED_DIRS)
        for name in sorted(files):
            if not include or fnmatch.fnmatch(name, include):
                yield os.path.join(root, name)


def _grep_file(path: str, display: str, regex: re.Pattern, context_lines: int, output_has_groups: bool) -> list[str]:
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return []
    if b"\0" in data[:8192]:
        return []  # binary
    lines = data.decode("utf-8", errors="replace").splitlines()
    matches = [i for i, line in enumerate(lines) if regex.search(line)]
    if not matches:
        return []
    shown: list[tuple[int, int]] = []  # merged [first, last] line ranges, matches plus their context
    for i in matches:
        first, last = max(i - context_lines, 0), min(i + context_lines, len(lines) - 1)
        if shown and first <= shown[-1][1] + 1:
            shown[-1] = (shown[-1][0], max(shown[-1][1], last))
        else:
            shown.append((first, last))
    matched = set(matches)
    result: list[str] = []
    for first, last in shown:
        if context_lines and (result or output_has_groups):
            result.append("--")
        for i in range(first, last + 1):
            separator = ":" if i in matched else "-"
            result.append(f"{display}{separator}{i + 1}{separator}{lines[i]}")
    return result


def ls_files(args: dict, scope: Target) -> str:
    workspace = _workspace(scope)
    target = _resolve(args.get("pattern") or ".", workspace)
    if not _readable(target, workspace):
        return f"Error: read access denied for '{target}'."
    if os.path.isdir(target):
        entries = sorted(os.listdir(target))
        listing = [entry + "/" if os.path.isdir(os.path.join(target, entry)) else entry for entry in entries]
        return f"{target}:\n" + ("\n".join(listing) if listing else "(empty)")
    matches = sorted(glob.glob(target, recursive=True))
    return _bound("\n".join(matches)) if matches else f"No files match '{target}'."


def edit_file(args: dict, scope: Target) -> str:
    workspace = _workspace(scope)
    full_path = _resolve(args.get("file_path", ""), workspace)
    search, replace = args.get("search", ""), args.get("replace", "")
    if not search:
        return "Error: search is required."
    if not _writable(full_path, workspace):
        return _write_denied(full_path, workspace)
    if not os.path.isfile(full_path):
        return f"Error: file not found: '{full_path}'. Use write_file to create new files."
    with open(full_path, "r", encoding="utf-8") as f:
        content = f.read()
    occurrences = content.count(search)
    if occurrences != 1:
        return (
            f"Error: search text found {occurrences} times in '{full_path}'; it must appear exactly once. "
            "Read the file and use a larger, unique snippet."
        )
    workspace.before_change(full_path)
    with open(full_path, "w", encoding="utf-8") as f:
        f.write(content.replace(search, replace, 1))
    _changed(full_path, workspace)
    return f"Edited '{full_path}'. The edited region now reads:\n" + _edit_snippet(content, search, replace)


def _edit_snippet(content: str, search: str, replace: str) -> str:
    """Numbered lines of the replacement plus a little context, so the edit needs no re-read."""
    lines = content.replace(search, replace, 1).split("\n")
    first = content[: content.index(search)].count("\n")
    last = first + replace.count("\n")
    start = max(first - EDIT_SNIPPET_CONTEXT_LINES, 0)
    end = min(last + EDIT_SNIPPET_CONTEXT_LINES + 1, len(lines), start + MAX_EDIT_SNIPPET_LINES)
    snippet = "\n".join(f"{start + i + 1}: {line}" for i, line in enumerate(lines[start:end]))
    return _bound(snippet)


def write_file(args: dict, scope: Target) -> str:
    workspace = _workspace(scope)
    full_path = _resolve(args.get("file_path", ""), workspace)
    if not _writable(full_path, workspace):
        return _write_denied(full_path, workspace)
    workspace.before_change(full_path)
    os.makedirs(os.path.dirname(full_path), exist_ok=True)
    with open(full_path, "w", encoding="utf-8") as f:
        f.write(args.get("content", ""))
    _changed(full_path, workspace)
    return f"Wrote '{full_path}'."


def delete_file(args: dict, scope: Target) -> str:
    workspace = _workspace(scope)
    full_path = _resolve(args.get("file_path", ""), workspace)
    if not _writable(full_path, workspace):
        return _write_denied(full_path, workspace)
    if not os.path.isfile(full_path):
        return f"Error: file not found: '{full_path}'."
    workspace.before_change(full_path)
    os.remove(full_path)
    _changed(full_path, workspace)
    return f"Deleted '{full_path}'."


def full_log_pointer(log_file_path: str | None) -> str:
    return f" Full log: {log_file_path} (search it with grep, passing that path as file_path)." if log_file_path else ""


def run_unit_tests(_args: dict, scope: Target) -> dict:
    """Returns a result dict: a short header in `output` and the raw failure output in
    `test_output`, which the server condenses (summarizing it when it is long)."""
    workspace = _workspace(scope)
    render_context = workspace.render_context
    exit_code, output, log_file_path = render_utils.execute_script(
        os.path.normpath(render_context.unittests_script),
        [render_context.build_folder],
        "Unit Tests",
        timeout=render_context.test_script_timeout,
        stop_event=render_context.stop_event,
    )
    context = render_context.unit_tests_running_context
    if exit_code == 0:
        context.verified_passing, context.verified_passing_log_path = True, log_file_path
        return {"output": "All unit tests passed."}
    if not log_file_path and output:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, suffix=".unittest_output") as f:
            f.write(output)
            log_file_path = f.name
    if log_file_path:
        register_log_path(log_file_path, workspace)
    return {
        "output": f"Unit tests failed (exit code {exit_code}).{full_log_pointer(log_file_path)}",
        "test_output": output,
    }


TOOLS: dict[str, Callable[[dict, Target], str | dict]] = {
    "read_file": read_file,
    "grep": grep,
    "ls_files": ls_files,
    "edit_file": edit_file,
    "write_file": write_file,
    "delete_file": delete_file,
    "run_unit_tests": run_unit_tests,
}


def execute_calls(calls: list[dict], scope: Target) -> list[dict]:
    """Execute the agent's tool calls in order; every call gets a result, errors included.

    A repeated read-only call (same tool and arguments, no file changed in between) is not
    re-executed: its result is already in the conversation, so a short pointer is returned."""
    workspace = _workspace(scope)
    cache = workspace.tool_result_cache
    results = []
    for call in calls:
        allowed = workspace.allowed_tools is None or call["name"] in workspace.allowed_tools
        tool = TOOLS.get(call["name"]) if allowed else None
        cache_key = json.dumps([call["name"], call.get("args") or {}], sort_keys=True)
        result: dict
        if tool is None:
            result = {"output": f"Error: unknown tool '{call['name']}'."}
        elif call["name"] in READ_ONLY_TOOLS and cache_key in cache:
            result = {
                "output": "Same call as an earlier one and no file has changed since; "
                "its result is unchanged (see the earlier result above)."
            }
        else:
            try:
                output = tool(call.get("args") or {}, workspace)
                result = output if isinstance(output, dict) else {"output": output}
            except Exception as e:
                result = {"output": f"Error: tool '{call['name']}' failed: {type(e).__name__}: {e}"}
            if call["name"] in READ_ONLY_TOOLS and not result["output"].startswith("Error"):
                cache[cache_key] = result["output"]
        console.debug(f"Agent tool {call['name']}({call.get('args')}) -> {result['output'][:200]!r}")
        results.append({"call_id": call["id"], **result})
    return results
