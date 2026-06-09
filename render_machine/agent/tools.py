import difflib
import os
import subprocess
from typing import Callable

import diff_utils
import file_utils
import render_machine.render_utils as render_utils
from render_machine.render_context import RenderContext

MAX_INLINE_OUTPUT_LINES = 200
DEFAULT_READ_LIMIT = 200
MAX_LINE_LENGTH = 10000  # Max characters per line to prevent context explosion


def run_unit_tests(args: dict, render_context: RenderContext) -> str:
    unittests_script = os.path.normpath(render_context.unittests_script)
    exit_code, output, temp_file_path = render_utils.execute_script(
        unittests_script,
        [render_context.build_folder],
        render_context.verbose,
        "Unit Tests",
        timeout=render_context.test_script_timeout,
        stop_event=render_context.stop_event,
    )
    if exit_code == 0:
        return "All unit tests passed successfully. The fix was successful, end the task and perform no further tool calls!"

    lines = output.split("\n") if output else []
    total_lines = len(lines)

    if total_lines <= MAX_INLINE_OUTPUT_LINES:
        return f"Tests failed (exit code {exit_code}):\n{output}"

    truncated = "\n".join(lines[-MAX_INLINE_OUTPUT_LINES:])
    if temp_file_path:
        return (
            f"Tests failed (exit code {exit_code}). "
            f"Full output of the tests is available at: {temp_file_path}\n"
        )
    else:
        return f"Tests failed (exit code {exit_code}):\n{truncated}"


def run_conformance_tests(args: dict, render_context: RenderContext) -> str:
    conformance_tests_script = os.path.normpath(render_context.conformance_tests_script)

    # Determine the conformance tests folder from render context
    ctx = render_context.conformance_tests_running_context
    if ctx and render_context.module_name == ctx.current_testing_module_name:
        conformance_tests_folder = ctx.get_current_conformance_test_folder_name()
    elif ctx:
        conformance_tests_folder, _ = render_context.conformance_tests.get_source_conformance_test_folder_name(
            render_context.module_name,
            render_context.required_modules,
            ctx.current_testing_module_name,
            ctx.get_current_conformance_test_folder_name(),
        )
    else:
        conformance_tests_folder = render_context.conformance_tests_folder

    script_args = [render_context.build_folder, conformance_tests_folder]

    exit_code, output, temp_file_path = render_utils.execute_script(
        conformance_tests_script,
        script_args,
        render_context.verbose,
        "Conformance Tests",
        timeout=render_context.test_script_timeout,
        stop_event=render_context.stop_event,
    )
    if exit_code == 0:
        return "All conformance tests passed successfully."

    lines = output.split("\n") if output else []
    total_lines = len(lines)

    if total_lines <= MAX_INLINE_OUTPUT_LINES:
        return f"Tests failed (exit code {exit_code}):\n{output}"

    truncated = "\n".join(lines[:MAX_INLINE_OUTPUT_LINES])
    if temp_file_path:
        return (
            f"Tests failed (exit code {exit_code}). Output truncated ({total_lines} total lines). "
            f"Full output available at: {temp_file_path}\n"
            f'Use read_file with file_path="{temp_file_path}" to see the complete output.\n\n{truncated}'
        )
    else:
        return f"Tests failed (exit code {exit_code}):\n{truncated}"


def prepare_environment(args: dict, render_context: RenderContext) -> str:
    if not render_context.prepare_environment_script:
        return "No environment preparation script configured."

    script = os.path.normpath(render_context.prepare_environment_script)
    exit_code, output, temp_file_path = render_utils.execute_script(
        script,
        [render_context.build_folder],
        render_context.verbose,
        "Testing Environment Preparation",
        timeout=render_context.test_script_timeout,
        stop_event=render_context.stop_event,
    )
    if exit_code == 0:
        return "Environment prepared successfully (compilation/build completed)."

    lines = output.split("\n") if output else []
    total_lines = len(lines)

    if total_lines <= MAX_INLINE_OUTPUT_LINES:
        return f"Environment preparation failed (exit code {exit_code}):\n{output}"

    truncated = "\n".join(lines[:MAX_INLINE_OUTPUT_LINES])
    if temp_file_path:
        return (
            f"Environment preparation failed (exit code {exit_code}). Output truncated ({total_lines} total lines). "
            f"Full output available at: {temp_file_path}\n"
            f'Use read_file with file_path="{temp_file_path}" to see the complete output.\n\n{truncated}'
        )
    else:
        return f"Environment preparation failed (exit code {exit_code}):\n{truncated}"


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
    """Return normalized absolute paths of folders the agent can read from."""
    return _get_allowed_write_folders(render_context)


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
        return f"Error reading '{full_path}': {e}"

    # Try exact match first
    if search_text in original_content:
        # Count occurrences
        count = original_content.count(search_text)
        if count > 1:
            return (
                f"Error: Search text found {count} times in '{full_path}'. "
                f"Please provide a more specific search string that uniquely identifies the text to replace."
            )

        # Perform replacement
        new_content = original_content.replace(search_text, replace_text, 1)

        # Write the updated content
        try:
            with open(full_path, "w", encoding="utf-8") as f:
                f.write(new_content)
            return f"Successfully edited '{full_path}' (exact match)"
        except PermissionError as e:
            return f"Error: Permission denied writing to '{full_path}': {e}"
        except Exception as e:
            return f"Error writing to '{full_path}': {e}"

    # Exact match failed - try fuzzy matching with whitespace normalization
    # This handles minor whitespace differences
    search_lines = search_text.splitlines()
    content_lines = original_content.splitlines()

    if not search_lines:
        return "Error: Search text is empty or contains only whitespace"

    best_ratio = 0.0
    best_match_start = -1
    best_match_end = -1

    # Sliding window to find best match
    for i in range(len(content_lines) - len(search_lines) + 1):
        window = content_lines[i : i + len(search_lines)]
        ratio = difflib.SequenceMatcher(None, search_lines, window).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best_match_start = i
            best_match_end = i + len(search_lines)

    # If we found a good fuzzy match (>90% similarity)
    if best_ratio > 0.9:
        matched_text = "\n".join(content_lines[best_match_start:best_match_end])

        # Perform replacement
        before = "\n".join(content_lines[:best_match_start])
        after = "\n".join(content_lines[best_match_end:])

        # Reconstruct content
        parts = []
        if before:
            parts.append(before)
        if replace_text:
            parts.append(replace_text)
        if after:
            parts.append(after)

        new_content = "\n".join(parts)

        # Preserve trailing newline if original had one
        if original_content.endswith("\n") and not new_content.endswith("\n"):
            new_content += "\n"

        # Write the updated content
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
            return f"Error writing to '{full_path}': {e}"

    # No good match found
    return (
        f"Error: Search text not found in '{full_path}'. "
        f"Best match was {best_ratio:.1%} similar (threshold is 90%).\n"
        f"Please verify the search text matches the actual file content. "
        f"Use read_file to view the current content."
    )


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

    try:
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, "w", encoding="utf-8") as f:
            f.write(content)
        return f"Successfully wrote '{full_path}'"
    except PermissionError as e:
        return f"Error: Permission denied writing to '{full_path}': {e}"
    except Exception as e:
        return f"Error writing to '{full_path}': {e}"


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

    # Delete the file
    try:
        os.remove(full_path)
        return f"Successfully deleted '{full_path}'"
    except PermissionError as e:
        return f"Error: Permission denied deleting '{full_path}': {e}"
    except Exception as e:
        return f"Error deleting '{full_path}': {e}"


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


def read_file(args: dict, render_context: RenderContext) -> str:
    file_path = args.get("file_path", "")
    offset = args.get("offset")
    limit = args.get("limit")

    if not file_path:
        return "Error: file_path is required"

    full_path = _resolve_file_path(file_path)
    allowed_folders = _get_allowed_read_folders(render_context)
    linked_resource_paths = _get_linked_resource_paths(render_context)

    # Check if file is within allowed folders or is a linked resource or a temp file
    is_in_allowed_folder = _is_within_any(full_path, allowed_folders)
    is_linked_resource = full_path in linked_resource_paths
    is_temp_file = full_path.startswith(os.path.normpath("/tmp") + os.sep) or full_path.startswith(
        os.path.normpath("/var/folders") + os.sep
    )

    if not is_in_allowed_folder and not is_linked_resource and not is_temp_file:
        folder_list = "\n".join(f"  - {f}" for f in allowed_folders)
        resource_list = ""
        if linked_resource_paths:
            resource_list = "\nLinked resource files:\n" + "\n".join(f"  - {p}" for p in linked_resource_paths)
        return (
            f"Error: Read access denied for '{file_path}' (resolved to '{full_path}').\n"
            f"You can read files within these folders:\n{folder_list}{resource_list}\n"
            f"You can also read temporary files (in /tmp/).\n"
            f"Use a relative path (resolved from '{_get_project_root()}') or an absolute path."
        )

    if not os.path.exists(full_path):
        return f"Error: File not found: '{full_path}'"

    try:
        with open(full_path, "r", encoding="utf-8") as f:
            all_lines = f.readlines()
    except PermissionError as e:
        return f"Error: Permission denied reading '{full_path}': {e}"
    except Exception as e:
        return f"Error reading '{full_path}': {e}"

    total_lines = len(all_lines)

    # Apply offset (1-based)
    start = max(0,total_lines-DEFAULT_READ_LIMIT)
    if offset is not None:
        start = max(0, int(offset) - 1)

    # Apply limit
    max_lines = int(limit) if limit is not None else DEFAULT_READ_LIMIT
    end = start + max_lines

    selected_lines = all_lines[start:end]

    # Truncate extremely long lines to prevent context explosion
    # (e.g., minified JS, base64, single-line JSON)
    truncated_lines = []
    for i, line in enumerate(selected_lines):
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
        return header + content
    elif total_lines > max_lines:
        header = f"[Showing lines 1-{max_lines} of {total_lines} total. Use offset/limit to read more. The rest of the file was truncated. If you want to read further, call read_file again with different offset and limit values.]\n "
        return header + "".join(all_lines[:max_lines])

    return content


def list_files(args: dict, render_context: RenderContext) -> str:
    directory_path = args.get("directory_path", "")

    if not directory_path:
        full_path = os.path.normpath(render_context.build_folder)
    else:
        full_path = _resolve_file_path(directory_path)

    allowed_folders = _get_allowed_read_folders(render_context)

    if not _is_within_any(full_path, allowed_folders):
        folder_list = "\n".join(f"  - {f}" for f in allowed_folders)
        return (
            f"Error: Access denied for '{directory_path}' (resolved to '{full_path}').\n"
            f"You can list files within these folders:\n{folder_list}\n"
            f"Use a relative path (resolved from '{_get_project_root()}') or an absolute path."
        )

    if not os.path.exists(full_path):
        return f"Error: Directory not found: '{full_path}'"

    if not os.path.isdir(full_path):
        return f"Error: Not a directory: '{full_path}'"

    try:
        files = file_utils.list_all_text_files(full_path)
    except PermissionError as e:
        return f"Error: Permission denied listing files in '{full_path}': {e}"
    except Exception as e:
        return f"Error listing files in '{full_path}': {e}"

    if not files:
        return "No files found in directory."
    return "\n".join(files)


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

    allowed_folders = _get_allowed_read_folders(render_context)
    resolved_search = os.path.normpath(os.path.abspath(search_path))

    # Check if path is within allowed folders or is a temp file
    is_in_allowed_folder = _is_within_any(resolved_search, allowed_folders)
    is_temp_file = resolved_search.startswith(os.path.normpath("/tmp") + os.sep) or resolved_search.startswith(
        os.path.normpath("/var/folders") + os.sep
    )

    if not is_in_allowed_folder and not is_temp_file:
        folder_list = "\n".join(f"  - {f}" for f in allowed_folders)
        return (
            f"Error: Access denied for '{file_path or 'build folder'}' (resolved to '{resolved_search}').\n"
            f"You can search within these folders:\n{folder_list}\n"
            f"You can also search temporary files (in /tmp/ or /var/folders/).\n"
            f"Use a relative path (resolved from '{_get_project_root()}') or an absolute path."
        )

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
        "--exclude-dir=dist",
        "--exclude-dir=build",
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
            return f"grep command failed (exit code {result.returncode}):\n{output}"

        if not output.strip():
            return f"No matches found for '{pattern}' in '{search_path}'"

        # Truncate if output is too long
        lines = output.strip().split("\n")
        if len(lines) > 100:
            return "\n".join(lines[:100]) + f"\n\n... ({len(lines) - 100} more matches truncated)"

        return output.strip()

    except subprocess.TimeoutExpired:
        return "Error: grep command timed out (>10 seconds)"
    except FileNotFoundError:
        return "Error: grep command not found (are you on Windows? Use WSL or Git Bash)"
    except Exception as e:
        return f"Error running grep: {e}"


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
            return f"Current directory: {current_dir}\nRan ls command: {shell_cmd}\n ls command failed (exit code {result.returncode}):\n{output}"

        if not output.strip():
            return f"Current directory: {current_dir}\nRan ls command: {shell_cmd}\n Directory is empty (no files found)"

        # Prepend current working directory to help agent understand context
        header = f"Current directory: {current_dir}\n"
        header += "Running ls command: " + shell_cmd + "\n"

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
        return f"Error running ls: {e}"


def think(args: dict, render_context: RenderContext) -> str:
    """Log the agent's reasoning or progress note to the user.

    Use this to narrate what you are doing, what you found, or what you are about to do next.
    Call it before major steps (exploration, planning, implementing) and after finding something
    significant. This is the primary way to keep the user informed during long tasks.

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


def create_submit_fix_for_review(
    file_snapshot: dict[str, str],
    specifications: str,
    acceptance_tests: str,
    test_failure: str,
    conformance_test_folder: str = "",
    conformance_tests_script: str = "",
) -> Callable[[dict, RenderContext], str]:
    """Factory that creates a submit_fix_for_review tool with captured context.

    The tool spins up a separate reviewer agent that can explore the code to verify
    the fix maintains engineering integrity.

    Args:
        file_snapshot: Dict of file_path → content at the time the fix started.
            Conformance test files are prefixed with "conformance_tests/".
        specifications: The spec text for the current frid.
        acceptance_tests: The acceptance test text.
        test_failure: The conformance test failure output.
        conformance_test_folder: Absolute path to the conformance tests folder.
    """

    def submit_fix_for_review(args: dict, render_context: RenderContext) -> str:
        from render_machine.agent.tool_executor import ToolExecutor

        explanation = args.get("explanation", "")

        # Compute diff between snapshot and current state
        current_files = {}
        all_impl_files = file_utils.list_all_text_files(render_context.build_folder)
        for file_path in all_impl_files:
            full_path = os.path.join(render_context.build_folder, file_path)
            with open(full_path, "r", encoding="utf-8") as f:
                current_files[file_path] = f.read()

        if conformance_test_folder and os.path.exists(conformance_test_folder):
            ct_files = file_utils.list_all_text_files(conformance_test_folder)
            for file_path in ct_files:
                full_path = os.path.join(conformance_test_folder, file_path)
                with open(full_path, "r", encoding="utf-8") as f:
                    current_files[f"conformance_tests/{file_path}"] = f.read()

        diff_text = diff_utils.get_code_diff(current_files, file_snapshot)
        if not diff_text:
            return "Rejected: No changes detected. Please write your fix before submitting for review."

        diff_str = ""
        for file_path, file_diff in diff_text.items():
            diff_str += f"--- {file_path}\n{file_diff}\n\n"

        # Build task params for the reviewer agent
        review_task_params = {
            "specifications": specifications,
            "acceptance_tests": acceptance_tests,
            "test_output": test_failure,
            "diff": diff_str,
            "explanation": explanation,
            "conformance_tests_script": conformance_tests_script,
        }

        # Reviewer gets read-only tools
        reviewer_tools = {
            "read_file": read_file,
            "list_files": list_files,
            "ls_files": ls_files,
            "grep": grep,
        }
        reviewer_executor = ToolExecutor(available_tools=reviewer_tools)

        # Run the reviewer agent to completion
        from render_machine.agent import agent_runner

        response = agent_runner.run(
            "review_conformance_fix",
            review_task_params,
            render_context,
            reviewer_executor,
        )

        # Parse the reviewer's final response for VERDICT
        result_text = response.get("result", "")
        if "VERDICT: APPROVED" in result_text.upper():
            return f"APPROVED. Reviewer feedback: {result_text}"
        else:
            return f"REJECTED. Reviewer feedback: {result_text}"

    return submit_fix_for_review
