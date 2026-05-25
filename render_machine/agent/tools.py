import os
import subprocess
from typing import Callable

import diff_utils
import file_utils
import render_machine.render_utils as render_utils
from render_machine.render_context import RenderContext

MAX_INLINE_OUTPUT_LINES = 200
TEST_OUTPUT_FILE = "_test_output.txt"
DEFAULT_READ_LIMIT = 200


def run_unit_tests(args: dict, render_context: RenderContext) -> str:
    unittests_script = os.path.normpath(render_context.unittests_script)
    exit_code, output, _ = render_utils.execute_script(
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

    # Save full output to file, return truncated version
    output_path = os.path.join(render_context.build_folder, TEST_OUTPUT_FILE)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(output)

    truncated = "\n".join(lines[:MAX_INLINE_OUTPUT_LINES])
    return (
        f"Tests failed (exit code {exit_code}). Output truncated ({total_lines} total lines). "
        f"Full output saved to {TEST_OUTPUT_FILE} (use read_file to see more).\n\n{truncated}"
    )


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

    exit_code, output, _ = render_utils.execute_script(
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

    output_path = os.path.join(render_context.build_folder, TEST_OUTPUT_FILE)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(output)

    truncated = "\n".join(lines[:MAX_INLINE_OUTPUT_LINES])
    return (
        f"Tests failed (exit code {exit_code}). Output truncated ({total_lines} total lines). "
        f"Full output saved to {TEST_OUTPUT_FILE} (use read_file to see more).\n\n{truncated}"
    )


def write_file(args: dict, render_context: RenderContext) -> str:
    file_path = args.get("file_path", "")
    content = args.get("content", "")
    full_path = os.path.join(render_context.build_folder, file_path)

    os.makedirs(os.path.dirname(full_path), exist_ok=True)
    with open(full_path, "w", encoding="utf-8") as f:
        f.write(content)
    return f"Successfully wrote {file_path}"


def read_file(args: dict, render_context: RenderContext) -> str:
    file_path = args.get("file_path", "")
    offset = args.get("offset")
    limit = args.get("limit")
    full_path = os.path.join(render_context.build_folder, file_path)

    if not os.path.exists(full_path):
        return f"Error: File '{file_path}' not found"

    with open(full_path, "r", encoding="utf-8") as f:
        all_lines = f.readlines()

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
    full_path = os.path.join(render_context.build_folder, directory_path)

    if not os.path.exists(full_path):
        return f"Error: Directory '{directory_path}' not found"

    files = file_utils.list_all_text_files(full_path)
    if not files:
        return "No files found in directory."
    return "\n".join(files)


def grep(args: dict, render_context: RenderContext) -> str:
    pattern = args.get("pattern", "")
    file_path = args.get("file_path", "")

    if not pattern:
        return "Error: pattern is required"

    search_path = os.path.join(render_context.build_folder, file_path) if file_path else render_context.build_folder

    if not os.path.exists(search_path):
        return f"Error: Path '{file_path}' not found"

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

    output = output.replace(render_context.build_folder + "/", "")

    lines = output.strip().split("\n")
    if len(lines) > 100:
        return "\n".join(lines[:100]) + f"\n\n... ({len(lines) - 100} more matches truncated)"
    return "\n".join(lines)


def create_submit_fix_for_review(
    file_snapshot: dict[str, str],
    specifications: str,
    acceptance_tests: str,
    test_failure: str,
    conformance_test_folder: str = "",
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
        }

        # Reviewer gets read-only tools
        reviewer_tools = {
            "read_file": read_file,
            "list_files": list_files,
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
