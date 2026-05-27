import os
import subprocess
from typing import Callable

import diff_utils
import file_utils
import render_machine.render_utils as render_utils
from render_machine.render_context import RenderContext

MAX_INLINE_OUTPUT_LINES = 200
DEFAULT_READ_LIMIT = 200

BASE_BUILD = "plain_modules"
BASE_CONFORMANCE_TESTS = "conformance_tests"
BASE_TEMP = "temp"
BASE_PROJECT = "project"


def _resolve_base_folder(base: str, render_context: RenderContext) -> str:
    """Resolve the base folder from the 'base' parameter.

    Args:
        base: "plain_modules" (default), "conformance_tests", "temp", or "project" (root).
        render_context: The current render context.

    Returns:
        Absolute path to the resolved folder, or empty string for temp (indicates absolute path usage).
    """
    if base == BASE_CONFORMANCE_TESTS:
        ctx = render_context.conformance_tests_running_context
        if ctx and render_context.module_name == ctx.current_testing_module_name:
            return ctx.get_current_conformance_test_folder_name()
        elif ctx:
            folder, _ = render_context.conformance_tests.get_source_conformance_test_folder_name(
                render_context.module_name,
                render_context.required_modules,
                ctx.current_testing_module_name,
                ctx.get_current_conformance_test_folder_name(),
            )
            return folder
        return render_context.conformance_tests_folder
    elif base == BASE_TEMP:
        # For temp files, return empty string to indicate absolute path usage
        return ""
    elif base == BASE_PROJECT:
        # Project root is the parent of build_folder (plain_modules)
        return os.path.dirname(render_context.build_folder)
    return render_context.build_folder


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
        return "All unit tests passed successfully."

    lines = output.split("\n") if output else []
    total_lines = len(lines)

    if total_lines <= MAX_INLINE_OUTPUT_LINES:
        return f"Tests failed (exit code {exit_code}):\n{output}"

    truncated = "\n".join(lines[:MAX_INLINE_OUTPUT_LINES])
    if temp_file_path:
        return (
            f"Tests failed (exit code {exit_code}). Output truncated ({total_lines} total lines). "
            f"Full output available at: {temp_file_path}\n"
            f'Use read_file with file_path="{temp_file_path}" and base="temp" to see the complete output.\n\n{truncated}'
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
            f'Use read_file with file_path="{temp_file_path}" and base="temp" to see the complete output.\n\n{truncated}'
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
            f'Use read_file with file_path="{temp_file_path}" and base="temp" to see the complete output.\n\n{truncated}'
        )
    else:
        return f"Environment preparation failed (exit code {exit_code}):\n{truncated}"


def write_file(args: dict, render_context: RenderContext) -> str:
    file_path = args.get("file_path", "")
    content = args.get("content", "")
    base = args.get("base", BASE_BUILD)

    # Validate file_path
    if not file_path:
        return f"Error: file_path is required"

    # Prevent writing to temp or project folders (read-only)
    if base == BASE_TEMP:
        return f"Error: Cannot write to temp folder. Use base='plain_modules' or base='conformance_tests'"
    if base == BASE_PROJECT:
        return f"Error: Cannot write to project root. Use base='plain_modules' or base='conformance_tests'"

    # Prevent absolute paths or parent directory traversal in file_path
    if os.path.isabs(file_path) or file_path.startswith("../"):
        return f"Error: file_path cannot be absolute or contain parent references (..), got: {file_path}"

    base_folder = _resolve_base_folder(base, render_context)
    full_path = os.path.join(base_folder, file_path)

    # Ensure full_path stays within base_folder (prevent path traversal)
    full_path = os.path.normpath(full_path)
    base_folder_normalized = os.path.normpath(base_folder)
    if not full_path.startswith(base_folder_normalized):
        return f"Error: file_path escapes base folder: {file_path}"

    try:
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, "w", encoding="utf-8") as f:
            f.write(content)
        return f"Successfully wrote {file_path} (base: {base})"
    except PermissionError as e:
        return f"Error: Permission denied writing to '{file_path}' (base: {base}): {e}"
    except Exception as e:
        return f"Error writing to '{file_path}' (base: {base}): {e}"


def read_file(args: dict, render_context: RenderContext) -> str:
    file_path = args.get("file_path", "")
    offset = args.get("offset")
    limit = args.get("limit")
    base = args.get("base", BASE_BUILD)
    base_folder = _resolve_base_folder(base, render_context)

    # Validate file_path
    if not file_path:
        return f"Error: file_path is required"

    # For temp files, file_path is absolute
    if base == BASE_TEMP:
        full_path = file_path
        if not os.path.isabs(full_path):
            return f"Error: When base='temp', file_path must be an absolute path, got: {file_path}"
    else:
        # Prevent absolute paths or parent directory traversal in file_path
        if os.path.isabs(file_path) or file_path.startswith("../"):
            return f"Error: file_path cannot be absolute or contain parent references (..), got: {file_path}"

        full_path = os.path.join(base_folder, file_path)

        # Ensure full_path stays within base_folder (prevent path traversal)
        full_path = os.path.normpath(full_path)
        base_folder_normalized = os.path.normpath(base_folder)
        if not full_path.startswith(base_folder_normalized):
            return f"Error: file_path escapes base folder: {file_path}"

    if not os.path.exists(full_path):
        return f"Error: File '{file_path}' not found (base: {base})"

    try:
        with open(full_path, "r", encoding="utf-8") as f:
            all_lines = f.readlines()
    except PermissionError as e:
        return f"Error: Permission denied reading file '{file_path}' (base: {base}): {e}"
    except Exception as e:
        return f"Error reading file '{file_path}' (base: {base}): {e}"

    total_lines = len(all_lines)

    # Apply offset (1-based)
    start = 0
    if offset is not None:
        start = max(0, int(offset) - 1)

    # Apply limit
    max_lines = int(limit) if limit is not None else DEFAULT_READ_LIMIT
    end = start + max_lines

    selected_lines = all_lines[start:end]
    content = "".join(selected_lines)

    # Add metadata if file was truncated
    if end < total_lines or start > 0:
        shown_start = start + 1
        shown_end = min(end, total_lines)
        header = f"[Showing lines {shown_start}-{shown_end} of {total_lines} total]\n"
        return header + content
    elif total_lines > max_lines:
        header = f"[Showing lines 1-{max_lines} of {total_lines} total. Use offset/limit to read more.]\n"
        return header + "".join(all_lines[:max_lines])

    return content


def list_files(args: dict, render_context: RenderContext) -> str:
    directory_path = args.get("directory_path", "")
    base = args.get("base", BASE_BUILD)
    base_folder = _resolve_base_folder(base, render_context)

    # Validate base_folder is not empty or root
    if not base_folder or base_folder == "/":
        return f"Error: Invalid base folder for base='{base}'"

    # Prevent absolute paths or parent directory traversal in directory_path
    if directory_path and (os.path.isabs(directory_path) or directory_path.startswith("../")):
        return f"Error: directory_path cannot be absolute or contain parent references (..), got: {directory_path}"

    full_path = os.path.join(base_folder, directory_path)

    # Ensure full_path stays within base_folder (prevent path traversal)
    full_path = os.path.normpath(full_path)
    base_folder_normalized = os.path.normpath(base_folder)
    if not full_path.startswith(base_folder_normalized):
        return f"Error: directory_path escapes base folder: {directory_path}"

    if not os.path.exists(full_path):
        return f"Error: Directory '{directory_path}' not found (base: {base})"

    if not os.path.isdir(full_path):
        return f"Error: '{directory_path}' is not a directory (base: {base})"

    try:
        files = file_utils.list_all_text_files(full_path)
    except PermissionError as e:
        return f"Error: Permission denied listing files in '{directory_path}' (base: {base}): {e}"
    except Exception as e:
        return f"Error listing files in '{directory_path}' (base: {base}): {e}"

    if not files:
        return "No files found in directory."
    return "\n".join(files)


def grep(args: dict, render_context: RenderContext) -> str:
    pattern = args.get("pattern", "")
    file_path = args.get("file_path", "")
    base = args.get("base", BASE_BUILD)

    if not pattern:
        return "Error: pattern is required"

    base_folder = _resolve_base_folder(base, render_context)

    # Prevent absolute paths or parent directory traversal in file_path
    if file_path and (os.path.isabs(file_path) or file_path.startswith("../")):
        return f"Error: file_path cannot be absolute or contain parent references (..), got: {file_path}"

    search_path = os.path.join(base_folder, file_path) if file_path else base_folder

    # Ensure search_path stays within base_folder (prevent path traversal)
    search_path = os.path.normpath(search_path)
    base_folder_normalized = os.path.normpath(base_folder)
    if not search_path.startswith(base_folder_normalized):
        return f"Error: file_path escapes base folder: {file_path}"

    if not os.path.exists(search_path):
        return f"Error: Path '{file_path}' not found (base: {base})"

    try:
        cmd = [
            "grep", "-rn",
            "--include=*.py", "--include=*.js", "--include=*.ts", "--include=*.java",
            "--include=*.cs", "--include=*.rb", "--include=*.go", "--include=*.rs",
            "--include=*.c", "--include=*.cpp", "--include=*.h", "--include=*.hpp",
            "--include=*.txt", "--include=*.json", "--include=*.yaml", "--include=*.yml",
            "--include=*.toml", "--include=*.cfg", "--include=*.ini",
            "--include=*.md", "--include=*.sh", "--include=*.bat", "--include=*.ps1",
            pattern, search_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    except subprocess.TimeoutExpired:
        return "Error: Search timed out"
    except Exception as e:
        return f"Error running grep: {e}"

    output = result.stdout
    if not output:
        return f"No matches found for '{pattern}'"

    output = output.replace(base_folder + "/", "")

    lines = output.strip().split("\n")
    if len(lines) > 100:
        return "\n".join(lines[:100]) + f"\n\n... ({len(lines) - 100} more matches truncated)"
    return "\n".join(lines)


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
        # Run the ls command (inherits current working directory from Python process)
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=10
        )

        # Combine stdout and stderr for complete output
        output = result.stdout
        if result.stderr:
            output += f"\n[stderr]: {result.stderr}"

        if result.returncode != 0:
            return f"Current directory: {current_dir}\nls command failed (exit code {result.returncode}):\n{output}"

        if not output.strip():
            return f"Current directory: {current_dir}\nDirectory is empty (no files found)"

        # Prepend current working directory to help agent understand context
        header = f"Current directory: {current_dir}\n"

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
