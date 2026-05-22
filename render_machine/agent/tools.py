import os
import subprocess

import file_utils
import render_machine.render_utils as render_utils
from render_machine.render_context import RenderContext


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
    return f"Tests failed (exit code {exit_code}):\n{output}"


def run_conformance_tests(args: dict, render_context: RenderContext) -> str:
    conformance_tests_script = os.path.normpath(render_context.conformance_tests_script)
    test_folder = args.get("test_folder", "")
    script_args = [render_context.build_folder]
    if test_folder:
        script_args.append(test_folder)

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
    return f"Tests failed (exit code {exit_code}):\n{output}"


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
    full_path = os.path.join(render_context.build_folder, file_path)

    if not os.path.exists(full_path):
        return f"Error: File '{file_path}' not found"
    with open(full_path, "r", encoding="utf-8") as f:
        return f.read()


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
