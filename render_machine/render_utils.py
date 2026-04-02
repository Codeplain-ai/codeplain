import os
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
from typing import Optional

import file_utils
import git_utils
import plain_spec
from plain2code_console import console
from plain2code_exceptions import RenderCancelledError

SCRIPT_EXECUTION_TIMEOUT = 120
TIMEOUT_ERROR_EXIT_CODE = 124
POLL_INTERVAL_SECONDS = 0.2
SIGTERM_GRACE_PERIOD_SECONDS = 0.2
STDOUT_READ_TIMEOUT_SECONDS = 5


def revert_changes_for_frid(render_context):
    if render_context.frid_context.frid is not None:
        previous_frid = plain_spec.get_previous_frid(render_context.plain_source_tree, render_context.frid_context.frid)
        git_utils.revert_to_commit_with_frid(render_context.build_folder, previous_frid)


def print_inputs(render_context, existing_files_content, message):
    tmp_resources_list = []
    plain_spec.collect_linked_resources(
        render_context.plain_source_tree,
        tmp_resources_list,
        [
            plain_spec.DEFINITIONS,
            plain_spec.NON_FUNCTIONAL_REQUIREMENTS,
            plain_spec.FUNCTIONAL_REQUIREMENTS,
        ],
        False,
        render_context.frid_context.frid,
    )
    console.print_resources(tmp_resources_list, render_context.frid_context.linked_resources)

    console.print_files(
        message,
        render_context.build_folder,
        existing_files_content,
        style=console.INPUT_STYLE,
    )


def _kill_process(proc: subprocess.Popen) -> None:
    """Kill a process and its entire process group."""
    if sys.platform != "win32":
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except OSError:
            proc.terminate()
    else:
        proc.terminate()
    try:
        proc.wait(timeout=SIGTERM_GRACE_PERIOD_SECONDS)
    except subprocess.TimeoutExpired:
        if sys.platform != "win32":
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except OSError:
                proc.kill()
        else:
            proc.kill()


def _sanitize_script_output(script_output: str) -> str:
    # this function removes the escape codes that clear the console
    clear_console_escape_codes_pattern = r"(?:\033\[[^a-zA-Z]*[a-zA-Z])*\033\[2J(?:\033\[[^a-zA-Z]*[a-zA-Z])*"

    pattern = re.compile(clear_console_escape_codes_pattern)
    parts = pattern.split(script_output)

    # take only the part after the last clear console escape code
    return parts[-1] if len(parts) > 1 else script_output


def execute_script(  # noqa: C901
    script: str,
    scripts_args: list[str],
    verbose: bool,
    script_type: str,
    frid: Optional[str] = None,
    module: Optional[str] = None,
    timeout: Optional[int] = None,
    stop_event: Optional[threading.Event] = None,
) -> tuple[int, str, Optional[str]]:
    temp_file_path = None
    script_timeout = timeout if timeout is not None else SCRIPT_EXECUTION_TIMEOUT

    script_path = file_utils.add_current_path_if_no_path(script)
    # On Windows, .ps1 files must be run via PowerShell, not as the executable
    if sys.platform == "win32" and script_path.lower().endswith(".ps1"):
        cmd = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", script_path] + scripts_args
    else:
        cmd = [script_path] + scripts_args

    start_time = time.time()
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        start_new_session=(sys.platform != "win32"),
    )

    try:
        while proc.poll() is None:
            if time.time() - start_time >= script_timeout:
                _kill_process(proc)
                partial_stdout = proc.stdout.read()
                exc = subprocess.TimeoutExpired(cmd, script_timeout)
                exc.stdout = partial_stdout
                raise exc
            if stop_event is not None:
                stop_event.wait(timeout=POLL_INTERVAL_SECONDS)
                if stop_event.is_set():
                    _kill_process(proc)
                    raise RenderCancelledError()
            else:
                time.sleep(POLL_INTERVAL_SECONDS)

        # Use communicate() with a timeout because child processes may hold the pipe open after the main process exits.
        try:
            stdout, _ = proc.communicate(timeout=STDOUT_READ_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            proc.stdout.close()
            stdout = ""
        elapsed_time = time.time() - start_time

        sanitized_script_output = _sanitize_script_output(stdout)

        # Log the info about the script execution
        if verbose:
            with tempfile.NamedTemporaryFile(
                mode="w+", encoding="utf-8", delete=False, suffix=".script_output"
            ) as temp_file:
                temp_file.write(f"\n═════════════════════════ {script_type} Script Output ═════════════════════════\n")
                temp_file.write(sanitized_script_output)
                temp_file.write("\n══════════════════════════════════════════════════════════════════════\n")
                temp_file_path = temp_file.name
                if proc.returncode != 0:
                    temp_file.write(f"{script_type} script {script} failed with exit code {proc.returncode}.\n")
                else:
                    temp_file.write(f"{script_type} script {script} successfully passed.\n")
                temp_file.write(f"{script_type} script execution time: {elapsed_time:.2f} seconds.\n")

            console.debug(f"[#888888]{script_type} script output stored in: {temp_file_path.strip()}[/#888888]")

            if proc.returncode != 0:
                if frid is not None:
                    console.debug(
                        f"The {script_type} script for functionality ID {frid} of module {module} has failed. Initiating the patching mode to automatically correct the discrepancies."
                    )
                else:
                    console.debug(
                        f"The {script_type} script has failed. Initiating the patching mode to automatically correct the discrepancies."
                    )
            else:
                if frid is not None:
                    console.info(
                        f"[#79FC96]The {script_type} script for functionality ID {frid} of module {module} has passed successfully.[/#79FC96]"
                    )
                else:
                    console.info(f"[#79FC96]All {script_type} scripts have passed successfully.[/#79FC96]")

        return proc.returncode, sanitized_script_output, temp_file_path

    except RenderCancelledError:
        raise
    except subprocess.TimeoutExpired as e:
        # Store timeout output in a temporary file
        if verbose:
            with tempfile.NamedTemporaryFile(
                mode="w+", encoding="utf-8", delete=False, suffix=".script_timeout"
            ) as temp_file:
                temp_file.write(f"{script_type} script {script} timed out after {script_timeout} seconds.")
                if e.stdout:
                    decoded_output = e.stdout.decode("utf-8") if isinstance(e.stdout, bytes) else e.stdout
                    temp_file.write(f"{script_type} script partial output before the timeout:\n{decoded_output}")
                else:
                    temp_file.write(f"{script_type} script did not produce any output before the timeout.")
                temp_file_path = temp_file.name
            console.warning(
                f"The {script_type} script timed out after {script_timeout} seconds. {script_type} script output stored in: {temp_file_path}"
            )

        return (
            TIMEOUT_ERROR_EXIT_CODE,
            f"{script_type} script did not finish in {script_timeout} seconds.",
            temp_file_path,
        )
