import subprocess
import tempfile
import time
from typing import Optional

import file_utils
import git_utils
import plain_spec
from plain2code_console import console

SCRIPT_EXECUTION_TIMEOUT = 120
TIMEOUT_ERROR_EXIT_CODE = 124


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


def execute_script(
    script: str,
    scripts_args: list[str],
    verbose: bool,
    script_type: str,
    frid: Optional[str] = None,
    timeout: Optional[int] = None,
) -> tuple[int, str, Optional[str]]:
    temp_file_path = None
    script_timeout = timeout if timeout is not None else SCRIPT_EXECUTION_TIMEOUT
    try:
        start_time = time.time()
        result = subprocess.run(
            [file_utils.add_current_path_if_no_path(script)] + scripts_args,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=script_timeout,
        )
        elapsed_time = time.time() - start_time
        # Log the info about the script execution
        if verbose:
            with tempfile.NamedTemporaryFile(mode="w+", delete=False, suffix=".script_output") as temp_file:
                temp_file.write(f"\n═════════════════════════ {script_type} Script Output ═════════════════════════\n")
                temp_file.write(result.stdout)
                temp_file.write("\n══════════════════════════════════════════════════════════════════════\n")
                temp_file_path = temp_file.name
                if result.returncode != 0:
                    temp_file.write(f"{script_type} script {script} failed with exit code {result.returncode}.\n")
                else:
                    temp_file.write(f"{script_type} script {script} successfully passed.\n")
                temp_file.write(f"{script_type} script execution time: {elapsed_time:.2f} seconds.\n")

            console.info(f"[#888888]{script_type} script output stored in: {temp_file_path.strip()}[/#888888]")

            if result.returncode != 0:
                if frid is not None:
                    console.info(
                        f"The {script_type} script for ID {frid} has failed. Initiating the patching mode to automatically correct the discrepancies."
                    )
                else:
                    console.info(
                        f"The {script_type} script has failed. Initiating the patching mode to automatically correct the discrepancies."
                    )
            else:
                if frid is not None:
                    console.info(f"[#79FC96]The {script_type} script for ID {frid} has passed successfully.[/#79FC96]")
                else:
                    console.info(f"[#79FC96]All {script_type} script passed successfully.[/#79FC96]")

        return result.returncode, result.stdout, temp_file_path
    except subprocess.TimeoutExpired as e:
        # Store timeout output in a temporary file
        if verbose:
            with tempfile.NamedTemporaryFile(mode="w+", delete=False, suffix=".script_timeout") as temp_file:
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
