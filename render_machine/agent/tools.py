import difflib
import os
import subprocess
import tempfile

import diff_utils
import render_machine.render_utils as render_utils
from render_machine.render_context import RenderContext

MAX_INLINE_OUTPUT_LINES = 200
DEFAULT_READ_LIMIT = 200
MAX_LINE_LENGTH = 10000  # Max characters per line to prevent context explosion
MAX_COMMAND_TIMEOUT = 300  # Hard cap (seconds) for an agent run_command call
# When run_command output exceeds MAX_INLINE_OUTPUT_LINES, keep this many lines from the
# head and tail (the middle is replaced with a TRUNCATED notice). Head+tail == the cap.
COMMAND_OUTPUT_HEAD_LINES = 100
COMMAND_OUTPUT_TAIL_LINES = 100
# Hard total budget (characters) for any tool result assembled from command/diff/grep
# output. Line-count caps alone do not bound size — a single minified-JS, base64 or
# JSON line can be megabytes — so this is the backstop that actually protects the
# context window. ~30k chars ≈ 7-8k tokens.
MAX_INLINE_OUTPUT_CHARS = 30_000

# Catastrophic command patterns refused by run_command as defense-in-depth. Commands
# run on the user's own machine against their own project, but a fixing agent should
# never need these, and refusing them guards against an accidental destructive call.
_DESTRUCTIVE_COMMAND_PATTERNS = (
    "rm -rf /",
    "rm -rf /*",
    "rm -rf ~",
    "mkfs",
    "shutdown",
    "reboot",
    "dd if=",
    ":(){",
    "> /dev/sd",
    "of=/dev/sd",
)


def _bound_inline_output(
    output: str,
    head_lines: int = COMMAND_OUTPUT_HEAD_LINES,
    tail_lines: int = COMMAND_OUTPUT_TAIL_LINES,
    max_line_chars: int = MAX_LINE_LENGTH,
    max_total_chars: int = MAX_INLINE_OUTPUT_CHARS,
) -> tuple[str, str]:
    """Bound arbitrary tool output before it is inlined into the model's context.

    Three layers, each catching what the previous one cannot:
      1. per-line cap — a single minified/base64/JSON line can be megabytes;
      2. line-count cap — keep the head (how it started) and tail (how it failed),
         drop the middle with an explicit notice;
      3. total character budget — the hard backstop, since 200 capped lines can
         still exceed any sane context allowance.

    Returns (bounded_text, truncation_note). The note is "" when nothing was cut;
    otherwise it describes what was cut so the caller can point the agent at the
    full output on disk.
    """
    total_chars = len(output)
    lines = output.split("\n")
    total_lines = len(lines)
    notes = []

    long_lines = 0
    capped_lines = []
    for line in lines:
        if len(line) > max_line_chars:
            long_lines += 1
            capped_lines.append(line[:max_line_chars] + f"... [line truncated, was {len(line):,} chars]")
        else:
            capped_lines.append(line)
    if long_lines:
        notes.append(f"{long_lines} line(s) over {max_line_chars:,} chars cut short")

    if total_lines > head_lines + tail_lines:
        omitted = total_lines - head_lines - tail_lines
        middle_notice = (
            f"══════════ TRUNCATED: lines {head_lines + 1}–{total_lines - tail_lines} "
            f"omitted ({omitted} lines) ══════════"
        )
        # list[-0:] would return the whole list, so guard the zero-tail case.
        tail_slice = capped_lines[-tail_lines:] if tail_lines else []
        capped_lines = capped_lines[:head_lines] + ["", middle_notice, ""] + tail_slice
        notes.append(f"showing first {head_lines} and last {tail_lines} of {total_lines} lines")

    text = "\n".join(capped_lines)

    if len(text) > max_total_chars:
        keep_head = int(max_total_chars * 0.6)
        keep_tail = max_total_chars - keep_head
        text = (
            text[:keep_head]
            + f"\n\n══════════ TRUNCATED: middle omitted, total budget {max_total_chars:,} chars ══════════\n\n"
            + text[-keep_tail:]
        )
        notes.append(f"capped at {max_total_chars:,} of {total_chars:,} chars")

    return text, "; ".join(notes)


def _spill_output_to_temp_file(output: str, suffix: str = ".tool_output") -> str | None:
    """Persist full tool output to a temp file so the agent can read_file the rest.

    Used when a tool truncated its inline result but has no temp file from the
    underlying runner. read_file applies its own per-line and per-call caps, so
    this is the safe consumption path for arbitrarily large output.
    """
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", delete=False, suffix=suffix) as f:
            f.write(output)
            return f.name
    except OSError:
        return None


def run_unit_tests(args: dict, render_context: RenderContext) -> str:  # noqa: U100
    unittests_script = os.path.normpath(render_context.unittests_script)
    exit_code, output, temp_file_path = render_utils.execute_script(
        unittests_script,
        [render_context.build_folder],
        "Unit Tests",
        timeout=render_context.test_script_timeout,
        stop_event=render_context.stop_event,
    )
    if exit_code == 0:
        # Do not claim the overall task is done here — in the conformance fix loop this
        # tool is a regression check, not the goal. Task completion semantics belong to
        # the task prompts. Also leave room to record a hard-won learning: right after
        # a fix is verified is exactly when key_learning-grade insight exists.
        return (
            "All unit tests passed successfully. If this completes your task, record any durable, "
            "non-obvious learning with write_memory and then finish; otherwise continue with your "
            "remaining work."
        )

    if not output:
        return f"Tests failed (exit code {exit_code}) with no output."

    # Failures and summaries live at the end of test-runner output, so the tail gets
    # most of the line budget.
    bounded, note = _bound_inline_output(output, head_lines=40, tail_lines=160)
    if not note:
        return f"Tests failed (exit code {exit_code}):\n{output}"

    if not temp_file_path:
        temp_file_path = _spill_output_to_temp_file(output, suffix=".unittest_output")
    pointer = (
        f" Full output is saved at: {temp_file_path} — use read_file for the parts you need." if temp_file_path else ""
    )
    return f"Tests failed (exit code {exit_code}). Output truncated ({note}).{pointer}\n{bounded}"


def _get_allowed_write_folders(render_context: RenderContext) -> list[str]:
    """Return normalized absolute paths of folders the agent can write to."""
    folders = [os.path.normpath(os.path.abspath(render_context.build_folder))]

    ctx = render_context.conformance_tests_running_context
    if ctx:
        if render_context.module_name == ctx.current_testing_module_name:
            folders.append(os.path.normpath(os.path.abspath(ctx.get_current_conformance_test_folder_name())))
        else:
            folder, _ = render_context.conformance_tests.get_source_conformance_test_folder_name(
                render_context.module_name,
                render_context.required_modules,
                ctx.current_testing_module_name,
                ctx.get_current_conformance_test_folder_name(),
            )
            folders.append(os.path.normpath(os.path.abspath(folder)))
    elif render_context.conformance_tests_folder:
        folders.append(os.path.normpath(os.path.abspath(render_context.conformance_tests_folder)))

    return folders


def _get_allowed_read_folders(render_context: RenderContext) -> list[str]:
    """Return normalized absolute paths of folders the agent can read from.

    This is the write set plus the module's memory folder. Agents may browse and read
    persisted memory notes (via read_file/grep/ls_files) on demand, but may only add to
    them through the dedicated write_memory tool — so the memory folder is intentionally
    readable here while staying out of the write set.
    """
    folders = list(_get_allowed_write_folders(render_context))
    memory_manager = getattr(render_context, "memory_manager", None)
    memory_folder = getattr(memory_manager, "memory_folder", None) if memory_manager else None
    if memory_folder:
        folders.append(os.path.normpath(os.path.abspath(memory_folder)))
    return folders


def _get_allowed_read_files(render_context: RenderContext) -> list[str]:
    """Return normalized absolute paths of individual files the agent may read.

    The test pipeline scripts (conformance tests, environment preparation, unit
    tests) live outside the build and conformance test folders, but the agent is
    instructed to read them to understand the test runners and build/compile steps.
    Allowlist them explicitly so read_file can access them.
    """
    scripts = [
        render_context.conformance_tests_script,
        render_context.prepare_environment_script,
        render_context.unittests_script,
    ]
    return [_resolve_file_path(s) for s in scripts if s]


def _get_modules_root(render_context: RenderContext) -> str:
    """Return the directory that holds all per-module build folders.

    Build folders are constructed as ``<modules_root>/<module_name>`` (e.g.
    ``plain_modules/module_2``), so the modules root is the build folder's parent. Its
    other children are sibling module folders, which contain confusing near-duplicates
    of the current build folder's code.
    """
    return os.path.dirname(os.path.normpath(os.path.abspath(render_context.build_folder)))


def _check_read_access(full_path: str, render_context: RenderContext) -> str | None:
    """Decide whether the agent may read ``full_path``. Returns None if allowed, else an error.

    Policy:
      - Always allow the explicitly-useful project paths: the build folder, the
        conformance tests folder, linked resources, the test pipeline scripts, and
        temp files.
      - Deny sibling module folders (anything under the modules root that is not the
        current build folder). Their code is a confusing near-duplicate of the current
        build folder, which already contains the merged code of the modules it builds on.
      - Allow everything else — notably libraries/dependencies (e.g. site-packages),
        so the agent can inspect third-party code when debugging.
    """
    if _is_within_any(full_path, _get_allowed_read_folders(render_context)):
        return None
    if full_path in _get_allowed_read_files(render_context):
        return None
    if full_path in _get_linked_resource_paths(render_context):
        return None
    if full_path.startswith(os.path.normpath("/tmp") + os.sep) or full_path.startswith(
        os.path.normpath("/var/folders") + os.sep
    ):
        return None

    modules_root = _get_modules_root(render_context)
    if _is_within_any(full_path, [modules_root]):
        build_folder = os.path.normpath(os.path.abspath(render_context.build_folder))
        return (
            f"Error: Read access denied for '{full_path}'. This path is inside another module's "
            f"folder under the modules root ('{modules_root}'). The current module's build folder "
            f"('{build_folder}') already contains the merged code of the modules it builds on — read "
            f"that copy instead of another module's folder."
        )

    # Outside the modules root: libraries, dependencies, system files, etc. — allowed.
    return None


def _get_project_root() -> str:
    """Return the project root directory (the process CWD)."""
    return os.getcwd()


def _resolve_file_path(file_path: str) -> str:
    """Resolve a file path to an absolute path.

    Relative paths are resolved against the project root (CWD).
    Absolute paths are returned as-is (normalized).
    """
    if os.path.isabs(file_path):
        return os.path.normpath(file_path)
    return os.path.normpath(os.path.join(_get_project_root(), file_path))


def _is_within_any(full_path: str, allowed_folders: list[str]) -> bool:
    """Check if full_path is within any of the allowed folders."""
    for folder in allowed_folders:
        if full_path == folder or full_path.startswith(folder + os.sep):
            return True
    return False


def _track_file_change(full_path: str, render_context: RenderContext):
    """Record original file content before modification for revert-on-rejection."""
    ctx = render_context.conformance_tests_running_context
    if ctx:
        ctx.track_file_before_modification(full_path)


def _describe_match_locations(original_content: str, search_text: str, max_locations: int = 5) -> str:
    """List the line numbers (with one line of preceding context) where search_text occurs.

    Included in the multiple-matches error so the model can disambiguate its search
    string in one shot instead of re-reading the file first.
    """
    locations = []
    start = 0
    while len(locations) < max_locations:
        index = original_content.find(search_text, start)
        if index == -1:
            break
        line_number = original_content.count("\n", 0, index) + 1
        lines = original_content.splitlines()
        preceding = lines[line_number - 2].strip() if line_number >= 2 else "(start of file)"
        locations.append(f"  - line {line_number} (preceded by: {preceding!r})")
        start = index + 1
    return "\n".join(locations)


def _do_exact_match(
    full_path: str, original_content: str, search_text: str, replace_text: str, render_context: RenderContext
) -> str:
    count = original_content.count(search_text)
    if count > 1:
        listed = _describe_match_locations(original_content, search_text)
        suffix = "" if count <= 5 else f"\n  (first 5 of {count} occurrences shown)"
        return (
            f"Error: Search text found {count} times in '{full_path}', at:\n{listed}{suffix}\n"
            f"Extend the search string with surrounding lines so it uniquely identifies "
            f"the occurrence to replace."
        )

    new_content = original_content.replace(search_text, replace_text, 1)
    _track_file_change(full_path, render_context)

    try:
        with open(full_path, "w", encoding="utf-8") as f:
            f.write(new_content)
        return f"Successfully edited '{full_path}' (exact match)"
    except PermissionError as e:
        return f"Error: Permission denied writing to '{full_path}': {e}"
    except Exception as e:
        return f"Error: failed to write to '{full_path}': {e}"


def _do_fuzzy_match(
    full_path: str, original_content: str, search_text: str, replace_text: str, render_context: RenderContext
) -> str:
    search_lines = search_text.splitlines()
    content_lines = original_content.splitlines()

    if not search_lines:
        return "Error: Search text is empty or contains only whitespace"

    best_ratio = 0.0
    best_match_start = -1
    best_match_end = -1

    # Also try windows slightly shorter/longer than the search text: a fixed-size
    # window can never match when the file differs from the search block by an
    # inserted or deleted line, no matter how similar the rest is.
    window_sizes = {size for size in range(len(search_lines) - 2, len(search_lines) + 3) if size >= 1}
    for window_size in window_sizes:
        for i in range(len(content_lines) - window_size + 1):
            window = content_lines[i : i + window_size]
            ratio = difflib.SequenceMatcher(None, search_lines, window).ratio()
            if ratio > best_ratio:
                best_ratio = ratio
                best_match_start = i
                best_match_end = i + window_size

    if best_ratio > 0.9:
        matched_text = "\n".join(content_lines[best_match_start:best_match_end])
        before = "\n".join(content_lines[:best_match_start])
        after = "\n".join(content_lines[best_match_end:])

        parts = []
        if before:
            parts.append(before)
        if replace_text:
            parts.append(replace_text)
        if after:
            parts.append(after)

        new_content = "\n".join(parts)

        if original_content.endswith("\n") and not new_content.endswith("\n"):
            new_content += "\n"

        _track_file_change(full_path, render_context)

        try:
            with open(full_path, "w", encoding="utf-8") as f:
                f.write(new_content)
            return (
                f"Successfully edited '{full_path}' (fuzzy match with {best_ratio:.1%} similarity).\n"
                f"Matched text:\n{matched_text[:200]}{'...' if len(matched_text) > 200 else ''}"
            )
        except PermissionError as e:
            return f"Error: Permission denied writing to '{full_path}': {e}"
        except Exception as e:
            return f"Error: failed to write to '{full_path}': {e}"

    closest = ""
    if best_match_start >= 0:
        closest_text = "\n".join(content_lines[best_match_start:best_match_end])
        if len(closest_text) > 2000:
            closest_text = closest_text[:2000] + "\n... [truncated]"
        closest = (
            f"\nThe closest section (lines {best_match_start + 1}-{best_match_end}) is:\n"
            f"{closest_text}\n"
            f"If this is the section you meant to edit, resend the edit using this exact text as the search string."
        )
    return (
        f"Error: Search text not found in '{full_path}'. "
        f"Best match was {best_ratio:.1%} similar (threshold is 90%).{closest}"
    )


def edit_file(args: dict, render_context: RenderContext) -> str:
    """Edit a file using search and replace with fuzzy matching for robustness.

    Args:
        args: Dictionary containing:
            - file_path (str): Path to the file to edit
            - search (str): Text to search for in the file
            - replace (str): Text to replace the search text with
        render_context: The current render context

    Returns:
        Success message or error description
    """
    file_path = args.get("file_path", "")
    search_text = args.get("search", "")
    replace_text = args.get("replace", "")

    if not file_path:
        return "Error: file_path is required"
    if not search_text:
        return "Error: search parameter is required"
    if "replace" not in args:  # Check if key exists, allow empty string
        return "Error: replace parameter is required (can be empty string for deletion)"

    # Check if search and replace are identical (no-op)
    if search_text == replace_text:
        return (
            f"Warning: No changes made to '{file_path}'. "
            f"The search and replace texts are identical. "
            f"If you intended to modify the file, please provide different replacement text."
        )

    full_path = _resolve_file_path(file_path)
    allowed_folders = _get_allowed_write_folders(render_context)

    if not _is_within_any(full_path, allowed_folders):
        folder_list = "\n".join(f"  - {f}" for f in allowed_folders)
        return (
            f"Error: Write access denied for '{file_path}' (resolved to '{full_path}').\n"
            f"You can only edit files within these folders:\n{folder_list}\n"
            f"Use a relative path (resolved from '{_get_project_root()}') or an absolute path."
        )

    # Check if file exists
    if not os.path.exists(full_path):
        return f"Error: File not found: '{full_path}'. Use write_file to create new files."

    # Read the current file content
    try:
        with open(full_path, "r", encoding="utf-8") as f:
            original_content = f.read()
    except PermissionError as e:
        return f"Error: Permission denied reading '{full_path}': {e}"
    except Exception as e:
        return f"Error: failed to read '{full_path}': {e}"

    # Try exact match first
    if search_text in original_content:
        return _do_exact_match(full_path, original_content, search_text, replace_text, render_context)

    # Exact match failed - try fuzzy matching with whitespace normalization
    # This handles minor whitespace differences
    return _do_fuzzy_match(full_path, original_content, search_text, replace_text, render_context)


def write_file(args: dict, render_context: RenderContext) -> str:
    file_path = args.get("file_path", "")
    content = args.get("content", "")

    if not file_path:
        return "Error: file_path is required"

    full_path = _resolve_file_path(file_path)
    allowed_folders = _get_allowed_write_folders(render_context)

    if not _is_within_any(full_path, allowed_folders):
        folder_list = "\n".join(f"  - {f}" for f in allowed_folders)
        return (
            f"Error: Write access denied for '{file_path}' (resolved to '{full_path}').\n"
            f"You can only write to files within these folders:\n{folder_list}\n"
            f"Use a relative path (resolved from '{_get_project_root()}') or an absolute path."
        )

    _track_file_change(full_path, render_context)

    try:
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, "w", encoding="utf-8") as f:
            f.write(content)
        return f"Successfully wrote '{full_path}'"
    except PermissionError as e:
        return f"Error: Permission denied writing to '{full_path}': {e}"
    except Exception as e:
        return f"Error: failed to write to '{full_path}': {e}"


def delete_file(args: dict, render_context: RenderContext) -> str:
    """Delete a file from the project.

    Args:
        args: Dictionary containing:
            - file_path (str): Path to the file to delete
        render_context: The current render context

    Returns:
        Success message or error description
    """
    file_path = args.get("file_path", "")

    if not file_path:
        return "Error: file_path is required"

    full_path = _resolve_file_path(file_path)
    allowed_folders = _get_allowed_write_folders(render_context)

    if not _is_within_any(full_path, allowed_folders):
        folder_list = "\n".join(f"  - {f}" for f in allowed_folders)
        return (
            f"Error: Delete access denied for '{file_path}' (resolved to '{full_path}').\n"
            f"You can only delete files within these folders:\n{folder_list}\n"
            f"Use a relative path (resolved from '{_get_project_root()}') or an absolute path."
        )

    # Check if file exists
    if not os.path.exists(full_path):
        return f"Error: File not found: '{full_path}'. Nothing to delete."

    # Check if it's a file (not a directory)
    if os.path.isdir(full_path):
        return f"Error: '{full_path}' is a directory. Use appropriate directory deletion if needed, or delete individual files."

    # Track original before deleting
    _track_file_change(full_path, render_context)

    # Delete the file
    try:
        os.remove(full_path)
        return f"Successfully deleted '{full_path}'"
    except PermissionError as e:
        return f"Error: Permission denied deleting '{full_path}': {e}"
    except Exception as e:
        return f"Error: failed to delete '{full_path}': {e}"


def _get_linked_resource_paths(render_context: RenderContext) -> list[str]:
    """Return normalized absolute paths for all linked resource files."""
    linked_resources = render_context.frid_context.linked_resources if render_context.frid_context else {}
    if not linked_resources:
        return []

    paths = []
    for resource_path in linked_resources.keys():
        full = os.path.normpath(os.path.join(_get_project_root(), resource_path))
        paths.append(full)
    return paths


def _rejects_full_conformance_suite(command: str, render_context: RenderContext) -> str | None:
    """Refuse commands that invoke the full conformance test suite.

    The full suite is expensive and is run automatically when the agent submits its
    fix, so the agent never needs to run it itself. Steer it toward targeted
    diagnostics instead. (The unit test script is cheaper and not guarded.)
    """
    script = render_context.conformance_tests_script
    if not script:
        return None
    candidates = {script, os.path.normpath(script), os.path.basename(script)}
    if any(c and c in command for c in candidates):
        return (
            "Error: Running the full conformance test suite via run_command is not allowed — it is "
            "expensive and runs automatically when you submit your fix. Use run_command for targeted "
            "diagnostics instead: reproduce the failing case directly (run a single function or a small "
            "snippet), inspect intermediate values, or probe a specific endpoint."
        )
    return None


def _rejects_destructive_command(command: str) -> str | None:
    """Refuse obviously catastrophic commands (defense-in-depth)."""
    lowered = command.lower()
    for pattern in _DESTRUCTIVE_COMMAND_PATTERNS:
        if pattern in lowered:
            return f"Error: command refused as potentially destructive (matched '{pattern}')."
    return None


def _snapshot_folder_files(folders: list[str]) -> set[str]:
    """Capture the set of file paths currently under the given folders."""
    snapshot: set[str] = set()
    for folder in folders:
        if not os.path.isdir(folder):
            continue
        for root, _dirs, names in os.walk(folder):
            for name in names:
                snapshot.add(os.path.join(root, name))
    return snapshot


def _remove_files_created_since(folders: list[str], before: set[str]) -> int:
    """Delete files under the given folders that were not present in the before-snapshot.

    Used to undo the file side effects of run_command (e.g. compiled .class files and
    other build artifacts produced by `mvn test` and the like) so they never leak into
    git diffs, per-FRID commits, or the existing-files content passed to agents. Only
    newly-created files are removed; pre-existing files — including the agent's own
    in-progress edits — are left untouched.
    """
    removed = 0
    for folder in folders:
        if not os.path.isdir(folder):
            continue
        for root, _dirs, names in os.walk(folder):
            for name in names:
                path = os.path.join(root, name)
                if path not in before:
                    try:
                        os.remove(path)
                        removed += 1
                    except OSError:
                        pass
    return removed


def run_command(args: dict, render_context: RenderContext) -> str:
    """Run an arbitrary shell command for diagnostics and return its combined output.

    The command runs from the project root. To run from elsewhere, prefix it with a cd
    (e.g. ``cd build && ...``). There is no working-directory restriction — a shell
    command can reach anywhere on the machine regardless, so a cwd guard would be
    meaningless; the only real guards are the suite/destructive refusals below.

    Any files the command creates under the build or conformance test folders are
    removed afterwards, so build artifacts (e.g. compiled .class files from `mvn test`)
    don't pollute diffs, commits, or the files passed to agents. Files created ANYWHERE
    ELSE — notably the project root (e.g. ``cp -R plain_modules/<module>/* .``) — are NOT
    cleaned up and will dirty the user's working tree, so the tool description steers the
    agent to keep all scratch work under /tmp (or point PYTHONPATH at the existing folders
    in place) rather than copying into the project. Write scratch files to /tmp if you need
    them to persist.

    Args:
        args: Dictionary containing:
            - command (str, required): The shell command to run.
            - timeout (int, optional): Seconds before the command is killed. Defaults to
              the standard command timeout, capped at MAX_COMMAND_TIMEOUT.
        render_context: The current render context.

    Returns:
        The exit code plus combined stdout/stderr (truncated, with the full output
        spilled to a temp file when large), or an error description.
    """
    command = args.get("command", "")
    if not command or not command.strip():
        return "Error: command is required"

    suite_rejection = _rejects_full_conformance_suite(command, render_context)
    if suite_rejection:
        return suite_rejection

    destructive_rejection = _rejects_destructive_command(command)
    if destructive_rejection:
        return destructive_rejection

    cwd = _get_project_root()

    # Resolve the timeout (capped).
    timeout = args.get("timeout")
    if timeout is not None:
        try:
            timeout = min(int(timeout), MAX_COMMAND_TIMEOUT)
        except (ValueError, TypeError):
            timeout = render_utils.COMMAND_EXECUTION_TIMEOUT
    else:
        timeout = render_utils.COMMAND_EXECUTION_TIMEOUT

    # Snapshot the tracked folders so any files the command creates (build artifacts
    # like compiled .class files) can be removed afterwards. These would otherwise be
    # picked up by git diffs / commits and leak into the context passed to agents.
    artifact_folders = _get_allowed_write_folders(render_context)
    files_before = _snapshot_folder_files(artifact_folders)
    try:
        exit_code, output, temp_file_path = render_utils.execute_command(
            command,
            cwd=cwd,
            timeout=timeout,
            stop_event=render_context.stop_event,
        )
    except Exception as e:
        return f"Error: command failed to run: {type(e).__name__}: {e}"
    finally:
        _remove_files_created_since(artifact_folders, files_before)

    header = f"Command exited with code {exit_code} (cwd: {cwd})."
    if not output:
        return f"{header}\n(no output)"

    open_divider = "──────────────────────────── command output ────────────────────────────"
    close_divider = "─────────────────────────── end command output ───────────────────────────"

    bounded, note = _bound_inline_output(output)

    # Untruncated output: show it all, just separated from the status line.
    if not note:
        return f"{header}\n{open_divider}\n{output}\n{close_divider}"

    # Something was cut — make sure the full output is on disk and say where.
    if not temp_file_path:
        temp_file_path = _spill_output_to_temp_file(output, suffix=".command_output")
    full_output_note = ""
    if temp_file_path:
        full_output_note = (
            f" The full output ({len(output):,} chars, {output.count(chr(10)) + 1} lines) is saved at: "
            f'{temp_file_path} — read the parts you need with read_file(file_path="{temp_file_path}").'
        )

    return (
        f"{header} Output truncated ({note}).{full_output_note}\n" f"{open_divider}\n" f"{bounded}\n" f"{close_divider}"
    )


def read_file(args: dict, render_context: RenderContext) -> str:
    file_path = args.get("file_path", "")
    offset = args.get("offset")
    limit = args.get("limit")

    if not file_path:
        return "Error: file_path is required"

    full_path = _resolve_file_path(file_path)

    denial = _check_read_access(full_path, render_context)
    if denial:
        return denial

    if not os.path.exists(full_path):
        return f"Error: File not found: '{full_path}'"

    try:
        with open(full_path, "r", encoding="utf-8") as f:
            all_lines = f.readlines()
    except PermissionError as e:
        return f"Error: Permission denied reading '{full_path}': {e}"
    except Exception as e:
        return f"Error: failed to read '{full_path}': {e}"

    total_lines = len(all_lines)

    # Apply offset (1-based). Without an offset, read from the beginning — this must
    # match the tool description the model sees ("If not provided, reads from the
    # beginning"); for source files the top (imports, class definitions) is what the
    # agent needs first.
    start = 0
    if offset is not None:
        start = max(0, int(offset) - 1)

    # Apply limit
    max_lines = int(limit) if limit is not None else DEFAULT_READ_LIMIT
    end = start + max_lines

    selected_lines = all_lines[start:end]

    # Truncate extremely long lines to prevent context explosion
    # (e.g., minified JS, base64, single-line JSON)
    truncated_lines = []
    for line in selected_lines:
        if len(line) > MAX_LINE_LENGTH:
            truncated = line[:MAX_LINE_LENGTH] + f"... [line truncated, {len(line)} total chars]\n"
            truncated_lines.append(truncated)
        else:
            truncated_lines.append(line)

    content = "".join(truncated_lines)

    # Add metadata if file was truncated
    if end < total_lines or start > 0:
        shown_start = start + 1
        shown_end = min(end, total_lines)
        header = f"[Showing lines {shown_start}-{shown_end} of {total_lines} total. The rest of the file was truncated. If you want to read further, call read_file again with different offset and limit values.]\n"
        if shown_end - shown_start > max_lines:
            header = "[The file is too large to display all lines!] " + header
        return header + "\n\n '```\n" + content + "\n```\n"

    return content


def _grep_artifact_dir_exclusions(render_context: RenderContext) -> list[str]:
    """Return artifact directory names grep may exclude without hiding project code.

    "build" and "dist" are common build-artifact directory names, but they are also
    plausible names for the actual build/output folders of this render (the default
    build folder is literally named "build"). Excluding them unconditionally makes a
    grep from the project root silently skip the entire implementation, so a name is
    only excluded when it does not appear as a path component of any folder the agent
    is meant to search (build folder, conformance tests folder, memory folder).
    """
    protected_components: set[str] = set()
    for folder in _get_allowed_read_folders(render_context):
        protected_components.update(os.path.normpath(folder).split(os.sep))
    return [name for name in ("build", "dist") if name not in protected_components]


def grep(args: dict, render_context: RenderContext) -> str:
    """Search for a pattern using the grep command.

    This tool directly executes the grep command with provided arguments.
    By default searches recursively with line numbers, excluding build artifacts and dependency directories.

    Args:
        args: Dictionary containing:
            - pattern (str, required): Pattern to search for.
            - file_path (str, optional): Path to search within. Defaults to build folder.
        render_context: The current render context.

    Returns:
        String containing grep output or error message.
    """
    pattern = args.get("pattern", "")
    file_path = args.get("file_path", "")

    if not pattern:
        return "Error: pattern is required"

    if not file_path:
        # Default to build folder
        search_path = render_context.build_folder
    else:
        search_path = _resolve_file_path(file_path)

    resolved_search = os.path.normpath(os.path.abspath(search_path))

    denial = _check_read_access(resolved_search, render_context)
    if denial:
        return denial

    if not os.path.exists(resolved_search):
        return f"Error: Path not found: '{resolved_search}'"

    # Build grep command
    cmd = [
        "grep",
        "-rn",  # recursive with line numbers
        "--exclude-dir=.git",
        "--exclude-dir=__pycache__",
        "--exclude-dir=node_modules",
        "--exclude-dir=.venv",
        "--exclude-dir=venv",
        "--exclude-dir=.mypy_cache",
        "--exclude-dir=.pytest_cache",
        *[f"--exclude-dir={name}" for name in _grep_artifact_dir_exclusions(render_context)],
        "--exclude=*.pyc",
        "--exclude=*.class",
        "--exclude=*.so",
        "--exclude=*.o",
        "--exclude=*.a",
        "--exclude=*.dll",
        "--exclude=*.exe",
        pattern,
        search_path,
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)

        # Combine stdout and stderr for complete output
        output = result.stdout
        if result.stderr:
            output += f"\n[stderr]: {result.stderr}"

        # grep returns exit code 1 when no matches found (not an error)
        if result.returncode == 1 and not output.strip():
            return f"No matches found for '{pattern}' in '{search_path}'"

        # Other non-zero exit codes are actual errors
        if result.returncode not in (0, 1):
            return f"Error: grep command failed (exit code {result.returncode}):\n{output}"

        if not output.strip():
            return f"No matches found for '{pattern}' in '{search_path}'"

        # Grep is for locating code, not reading it — a match on a minified or
        # data line would otherwise inline that entire line. Cap match lines hard
        # (500 chars is plenty to identify a location) and bound the total; the
        # agent can read_file the location or narrow the pattern for more.
        bounded, note = _bound_inline_output(output.strip(), head_lines=100, tail_lines=0, max_line_chars=500)
        if note:
            return f"{bounded}\n\n[Output truncated ({note}) — narrow the pattern or read_file specific locations.]"
        return bounded

    except subprocess.TimeoutExpired:
        return "Error: grep command timed out (>10 seconds)"
    except FileNotFoundError:
        return "Error: grep command not found (are you on Windows? Use WSL or Git Bash)"
    except Exception as e:
        return f"Error: grep failed to run: {e}"


def ls_files(args: dict, render_context: RenderContext) -> str:
    """Permissive ls command wrapper that allows any pattern the agent decides.

    This tool directly executes the ls command with the provided pattern.
    Use this when you need flexible file listing with wildcards, absolute paths,
    or other advanced ls features.

    Args:
        args: Dictionary containing:
            - pattern (str, optional): Pattern/path to pass to ls command.
                Can be a glob pattern, absolute path, relative path, or empty.
                Examples: "*.py", "/etc/", "../src/*.js", "."
                If empty, lists current directory contents.
            - options (str, optional): Additional ls options (e.g., "-la", "-lh", "-R")
                Defaults to "" (no options) for simple listing.
        render_context: The current render context.

    Returns:
        String containing ls output or error message.
    """
    pattern = args.get("pattern", "")
    options = args.get("options", "")

    # Enforce the same read-access policy as read_file/grep: resolve the target the
    # listing points at and deny sibling-module folders. (An empty pattern lists the
    # project root, which is allowed.)
    if pattern:
        denial = _check_read_access(_resolve_file_path(pattern), render_context)
        if denial:
            return denial

    # Build ls command
    cmd = ["ls"]

    # Add options if provided
    if options:
        # Split options in case multiple are provided (e.g., "-l -a")
        cmd.extend(options.split())

    # Add pattern/path if provided, otherwise ls will list current directory
    if pattern:
        cmd.append(pattern)

    # Get the actual current working directory of the Python process
    current_dir = os.getcwd()

    try:
        # Run the ls command with shell=True to enable glob expansion
        # Join command parts into a single string for shell execution
        shell_cmd = " ".join(cmd)
        result = subprocess.run(shell_cmd, capture_output=True, text=True, timeout=10, shell=True, cwd=current_dir)

        # Combine stdout and stderr for complete output
        output = result.stdout
        if result.stderr:
            output += f"\n[stderr]: {result.stderr}"

        if result.returncode != 0:
            return f"Error: Ran ls command: {shell_cmd}\n ls command failed (exit code {result.returncode}):\n{output}.\n Current directory: {current_dir}\n"

        if not output.strip():
            return f"Error: Ran ls command: {shell_cmd}\n Directory is empty (no files found).\n Current directory: {current_dir}\n"

        # Prepend current working directory to help agent understand context
        header = "Ran ls command: " + shell_cmd + "\n"

        # Truncate if output is too long
        lines = output.strip().split("\n")
        if len(lines) > 200:
            return header + "\n".join(lines[:200]) + f"\n\n... ({len(lines) - 200} more lines truncated)"

        return header + output.strip()

    except subprocess.TimeoutExpired:
        return "Error: ls command timed out (>10 seconds)"
    except FileNotFoundError:
        return "Error: ls command not found (are you on Windows? Use WSL or Git Bash)"
    except Exception as e:
        return f"Error: ls failed to run: {e}"


def get_session_changes(args: dict, render_context: RenderContext) -> str:  # noqa: U100
    """Return the cumulative diff of all changes made during the current fix loop.

    Computed from the file change tracker (original content captured before the first
    modification of each file, accumulated across fix attempts since the last accepted
    fix), so it reflects the true current state — unlike the diffs the session was
    seeded with, which go stale as soon as editing starts.
    """
    ctx = render_context.conformance_tests_running_context
    if not ctx or not ctx.file_change_tracker:
        return "No changes have been made in this fix session yet."

    current_files: dict[str, str] = {}
    original_files: dict[str, str] = {}
    new_files: set[str] = set()
    for absolute_path, original_content in ctx.file_change_tracker.items():
        relative_path = os.path.relpath(absolute_path)
        if original_content is not None:
            original_files[relative_path] = original_content
        else:
            new_files.add(relative_path)
        if os.path.exists(absolute_path):
            with open(absolute_path, "r", encoding="utf-8") as f:
                current_files[relative_path] = f.read()
        else:
            current_files[relative_path] = ""

    diff_by_file = diff_utils.get_code_diff(current_files, original_files)
    if not diff_by_file:
        return "Files were touched during this fix session, but their content is back to the pre-fix baseline."

    parts = ["Cumulative changes in this fix session (vs the pre-fix baseline):"]
    for file_name, file_diff in diff_by_file.items():
        if file_name in new_files:
            parts.append(f"--- {file_name} (new file)\n{file_diff}")
        else:
            parts.append(f"--- {file_name}\n{file_diff}")
    output = "\n\n".join(parts)

    bounded, note = _bound_inline_output(output, head_lines=600, tail_lines=200)
    if note:
        spill_path = _spill_output_to_temp_file(output, suffix=".session_diff")
        pointer = f" The full diff is saved at: {spill_path} — use read_file for the rest." if spill_path else ""
        return f"{bounded}\n\n[Diff truncated ({note}).{pointer}]"
    return bounded


def report_progress(args: dict, render_context: RenderContext) -> str:  # noqa: U100
    """Log the agent's reasoning or progress note to the user.

    Use this to narrate what you are doing, what you found, or what you are about to do next.
    Call it before major steps (exploration, planning, implementing) and after finding something
    significant. This is the primary way to keep the user informed during long tasks.

    Previously named "think" — renamed because models treat "think" as a private
    scratchpad rather than user-facing narration (and Gemini has native thinking
    anyway). The old name stays registered as an alias for older servers.

    Args:
        args: Dictionary containing:
            - message (str, required): The thought or progress note to display to the user.
        render_context: The current render context.

    Returns:
        Empty string (acknowledgement).
    """
    from plain2code_console import console

    message = args.get("message", "")
    if message:
        console.info(f"[Agent] {message}")
    return ""


def write_memory(args: dict, render_context: RenderContext) -> str:
    """Write a persistent memory note for future fixing agents.

    Notes are stored in the module's agent memory folder and injected into the context
    of future agent sessions. Writing to an existing file name overwrites the note.

    Args:
        args: Dictionary containing:
            - file_name (str, required): Plain file name for the note (no directories).
            - content (str, required): The note content.
        render_context: The current render context.

    Returns:
        Success message or error description.
    """
    from memory_management import MemoryManager

    file_name = args.get("file_name", "")
    content = args.get("content", "")

    if not file_name:
        return "Error: file_name is required"
    if not content:
        return "Error: content is required"

    if os.path.basename(file_name) != file_name or file_name in (".", ".."):
        return f"Error: file_name must be a plain file name without directories, got '{file_name}'"

    try:
        full_path = MemoryManager.write_agent_memory_file(
            render_context.memory_manager.memory_folder, file_name, content
        )
        return f"Memory note saved to '{full_path}'. It will be available to future fixing agents."
    except Exception as e:
        return f"Error: failed to write memory note '{file_name}': {e}"
