"""Client-side implementations of the tools a server-side agent can call.

The server declares the tools to the LLM (codeplain-api: src/agent/tools.py) and forwards
the model's calls; this module executes them against the local build folder and returns
plain-text results. Relative paths resolve against the build folder. Reads are allowed in the
build folder and the project root (the CWD); writes only inside the build folder.
"""

import glob
import json
import os
import subprocess
import tempfile
from typing import Callable

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


def _build_folder(render_context: RenderContext) -> str:
    return os.path.normpath(os.path.abspath(render_context.build_folder))


def _resolve(file_path: str, render_context: RenderContext) -> str:
    if os.path.isabs(file_path):
        return os.path.normpath(file_path)
    return os.path.normpath(os.path.join(_build_folder(render_context), file_path))


def _within(path: str, folder: str) -> bool:
    return path == folder or path.startswith(folder + os.sep)


def _readable(path: str, render_context: RenderContext) -> bool:
    return (
        _within(path, _build_folder(render_context))
        or _within(path, os.path.normpath(os.getcwd()))
        or path in render_context.unit_tests_agent_session.readable_log_paths
    )


def register_log_path(log_path: str, render_context: RenderContext) -> None:
    """Allow read_file/grep on a full test log the agent is pointed to."""
    render_context.unit_tests_agent_session.readable_log_paths.add(os.path.normpath(os.path.abspath(log_path)))


def _writable(path: str, render_context: RenderContext) -> bool:
    return _within(path, _build_folder(render_context))


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
    context.changed_files.add(os.path.relpath(full_path, _build_folder(render_context)))
    context.verified_passing, context.verified_passing_log_path = False, None
    context.tool_result_cache.clear()


def read_file(args: dict, render_context: RenderContext) -> str:
    full_path = _resolve(args.get("file_path", ""), render_context)
    if not _readable(full_path, render_context):
        return f"Error: read access denied for '{full_path}' (readable: build folder and project root)."
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


def grep(args: dict, render_context: RenderContext) -> str:
    pattern = args.get("pattern", "")
    if not pattern:
        return "Error: pattern is required."
    target = _resolve(args.get("file_path") or ".", render_context)
    if not _readable(target, render_context):
        return f"Error: read access denied for '{target}'."
    if not os.path.exists(target):
        return f"Error: path not found: '{target}'."
    # Run from the build folder so matches inside it come back as build-relative paths, which
    # is the form the other tools accept.
    build_folder = _build_folder(render_context)
    cwd = build_folder if _within(target, build_folder) else os.getcwd()
    options = [f"--exclude-dir={d}" for d in GREP_EXCLUDED_DIRS]
    context_lines = min(max(int(args.get("context_lines") or 0), 0), MAX_GREP_CONTEXT_LINES)
    if context_lines:
        options.append(f"-C{context_lines}")
    if args.get("include"):
        options.append(f"--include={args['include']}")
    command = ["grep", "-rnI", *options, "-e", pattern, "--"]
    command.append(os.path.relpath(target, cwd) if _within(target, cwd) else target)
    result = subprocess.run(command, capture_output=True, text=True, cwd=cwd)
    if result.returncode == 1:
        return f"No matches for '{pattern}' in '{target}'."
    if result.returncode != 0:
        return f"Error: grep failed: {result.stderr.strip()}"
    lines = [line[2:] if line.startswith("./") else line for line in result.stdout.rstrip("\n").split("\n")]
    return _bound("\n".join(lines))


def ls_files(args: dict, render_context: RenderContext) -> str:
    target = _resolve(args.get("pattern") or ".", render_context)
    if not _readable(target, render_context):
        return f"Error: read access denied for '{target}'."
    if os.path.isdir(target):
        entries = sorted(os.listdir(target))
        listing = [entry + "/" if os.path.isdir(os.path.join(target, entry)) else entry for entry in entries]
        return f"{target}:\n" + ("\n".join(listing) if listing else "(empty)")
    matches = sorted(glob.glob(target, recursive=True))
    return _bound("\n".join(matches)) if matches else f"No files match '{target}'."


def edit_file(args: dict, render_context: RenderContext) -> str:
    full_path = _resolve(args.get("file_path", ""), render_context)
    search, replace = args.get("search", ""), args.get("replace", "")
    if not search:
        return "Error: search is required."
    if not _writable(full_path, render_context):
        return f"Error: write access denied for '{full_path}' (writable: build folder only)."
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
    with open(full_path, "w", encoding="utf-8") as f:
        f.write(content.replace(search, replace, 1))
    _track_change(full_path, render_context)
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


def write_file(args: dict, render_context: RenderContext) -> str:
    full_path = _resolve(args.get("file_path", ""), render_context)
    if not _writable(full_path, render_context):
        return f"Error: write access denied for '{full_path}' (writable: build folder only)."
    os.makedirs(os.path.dirname(full_path), exist_ok=True)
    with open(full_path, "w", encoding="utf-8") as f:
        f.write(args.get("content", ""))
    _track_change(full_path, render_context)
    return f"Wrote '{full_path}'."


def delete_file(args: dict, render_context: RenderContext) -> str:
    full_path = _resolve(args.get("file_path", ""), render_context)
    if not _writable(full_path, render_context):
        return f"Error: write access denied for '{full_path}' (writable: build folder only)."
    if not os.path.isfile(full_path):
        return f"Error: file not found: '{full_path}'."
    os.remove(full_path)
    _track_change(full_path, render_context)
    return f"Deleted '{full_path}'."


def full_log_pointer(log_file_path: str | None) -> str:
    return f" Full log: {log_file_path} (search it with grep, passing that path as file_path)." if log_file_path else ""


def run_unit_tests(_args: dict, render_context: RenderContext) -> dict:
    """Returns a result dict: a short header in `output` and the raw failure output in
    `test_output`, which the server condenses (summarizing it when it is long)."""
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
        register_log_path(log_file_path, render_context)
    return {
        "output": f"Unit tests failed (exit code {exit_code}).{full_log_pointer(log_file_path)}",
        "test_output": output,
    }


TOOLS: dict[str, Callable[[dict, RenderContext], str | dict]] = {
    "read_file": read_file,
    "grep": grep,
    "ls_files": ls_files,
    "edit_file": edit_file,
    "write_file": write_file,
    "delete_file": delete_file,
    "run_unit_tests": run_unit_tests,
}


def execute_calls(calls: list[dict], render_context: RenderContext) -> list[dict]:
    """Execute the agent's tool calls in order; every call gets a result, errors included.

    A repeated read-only call (same tool and arguments, no file changed in between) is not
    re-executed: its result is already in the conversation, so a short pointer is returned."""
    cache = render_context.unit_tests_running_context.tool_result_cache
    results = []
    for call in calls:
        tool = TOOLS.get(call["name"])
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
                output = tool(call.get("args") or {}, render_context)
                result = output if isinstance(output, dict) else {"output": output}
            except Exception as e:
                result = {"output": f"Error: tool '{call['name']}' failed: {type(e).__name__}: {e}"}
            if call["name"] in READ_ONLY_TOOLS and not result["output"].startswith("Error"):
                cache[cache_key] = result["output"]
        console.debug(f"Agent tool {call['name']}({call.get('args')}) -> {result['output'][:200]!r}")
        results.append({"call_id": call["id"], **result})
    return results
