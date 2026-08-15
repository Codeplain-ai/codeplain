import sys
import tempfile
import threading
import time
from typing import Optional

import file_utils
import plain_spec
from plain2code_console import MUTED_COLOR, RETRY_COLOR, SUCCESS_COLOR, console
from plain2code_exceptions import RenderCancelledError
from render_machine.terminal_process import (
    ENVIRONMENT_ERROR_EXIT_CODE,
    TerminalProcess,
    TerminalProcessError,
    TerminalReaderError,
    create_terminal_process,
)

SCRIPT_EXECUTION_TIMEOUT = 120
TIMEOUT_ERROR_EXIT_CODE = 124
POLL_INTERVAL_SECONDS = 0.2

# The `codeplain-tty` broker that would drive a script's terminal input is deferred, so no
# input driver is ever attached. The timeout diagnostic is keyed on this declaration rather
# than on bytes written: a script that blocks on input has written nothing either way.
INPUT_DRIVER: Optional[object] = None

NO_INPUT_DIAGNOSTIC = (
    " No input driver was attached to the script's terminal, so a script that waits for input "
    "never receives any and runs to the timeout."
)

# Conditions the arbiter chooses between, highest precedence last.
CONDITION_EXIT = "exit"
CONDITION_TIMEOUT = "timeout"
CONDITION_CANCELLED = "cancelled"
CONDITION_INFRASTRUCTURE = "infrastructure"

_CONDITION_RANK = {
    CONDITION_EXIT: 0,
    CONDITION_TIMEOUT: 1,
    CONDITION_CANCELLED: 2,
    CONDITION_INFRASTRUCTURE: 3,
}


def revert_changes_for_frid(render_context):
    if render_context.frid_context.frid is not None:
        previous_frid = plain_spec.get_previous_frid(render_context.plain_source_tree, render_context.frid_context.frid)
        render_context.plain_module.revert_code_to_frid(previous_frid)


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


class _ScriptOutcome:
    """The single place a script execution's primary condition is decided.

    Teardown always runs before publication and may still add evidence, so conditions are
    ranked rather than assigned in whatever order they happen to be discovered: an
    independent infrastructure failure outranks an observed cancellation, which outranks
    the expired deadline, which outranks the target's own exit status. Workers publish
    facts; only the foreground records a condition here.
    """

    def __init__(self) -> None:
        self.condition = CONDITION_EXIT
        self.exit_code: Optional[int] = None
        self.detail = ""

    def target_exited(self, exit_code: int) -> None:
        self.exit_code = exit_code

    def timed_out(self) -> None:
        self._record(CONDITION_TIMEOUT)

    def cancelled(self) -> None:
        self._record(CONDITION_CANCELLED)

    def infrastructure_failed(self, detail: str) -> None:
        self._record(CONDITION_INFRASTRUCTURE, detail)

    def _record(self, condition: str, detail: str = "") -> None:
        if _CONDITION_RANK[condition] < _CONDITION_RANK[self.condition]:
            return
        if condition == self.condition and self.detail:
            return  # the first evidence of a condition is the one that explains it
        self.condition = condition
        self.detail = detail


class _ScriptExecution:
    """Everything publication needs, gathered once the backend has been torn down."""

    def __init__(self) -> None:
        self.outcome = _ScriptOutcome()
        self.output = ""
        self.raw_output = b""
        self.reply_failed = False
        self.reply_detail = ""


def _await_target(
    process: TerminalProcess,
    script_timeout: float,
    stop_event: Optional[threading.Event],
    outcome: _ScriptOutcome,
) -> None:
    """Waits for the target, recording whichever condition ends the wait."""
    deadline = time.monotonic() + script_timeout
    while True:
        returncode = process.poll()
        if returncode is not None:
            outcome.target_exited(returncode)
            return
        if process.reader_failed.is_set():
            raise TerminalReaderError(f"the terminal output reader failed: {process.reader_exc!r}")
        if time.monotonic() >= deadline:
            outcome.timed_out()
            return
        if stop_event is not None:
            stop_event.wait(timeout=POLL_INTERVAL_SECONDS)
            if stop_event.is_set():
                raise RenderCancelledError()
        else:
            time.sleep(POLL_INTERVAL_SECONDS)


def _teardown(process: TerminalProcess, outcome: _ScriptOutcome) -> None:
    """Releases every handle the backend owns, then classifies what teardown revealed."""
    try:
        try:
            process.terminate_tree()
        finally:
            process.close()
    except TerminalProcessError as exc:
        outcome.infrastructure_failed(str(exc))
    except Exception as exc:
        outcome.infrastructure_failed(f"the terminal backend failed while shutting down: {exc!r}")
    # Deliberately checked after teardown and at the highest precedence: a reader that
    # died independently is an environment failure even when it surfaces while a timeout
    # or a cancellation is being cleaned up.
    if process.reader_failed.is_set():
        outcome.infrastructure_failed(f"the terminal output reader failed: {process.reader_exc!r}")


def _run_script(cmd: list[str], script_timeout: float, stop_event: Optional[threading.Event]) -> _ScriptExecution:
    execution = _ScriptExecution()
    process: Optional[TerminalProcess] = None
    try:
        process = create_terminal_process()
        try:
            process.spawn(cmd, stop_event=stop_event, input_driver=INPUT_DRIVER)
            _await_target(process, script_timeout, stop_event, execution.outcome)
        finally:
            _teardown(process, execution.outcome)
    except RenderCancelledError:
        execution.outcome.cancelled()
    except TerminalProcessError as exc:
        execution.outcome.infrastructure_failed(str(exc))
    if process is not None:
        execution.output = process.normalized_output()
        execution.raw_output = process.read_raw_output()
        execution.reply_failed = process.terminal_reply_failed
        execution.reply_detail = process.terminal_reply_detail()
    return execution


def _store_raw_output(script_type: str, raw_output: bytes) -> None:
    """Keeps the unrendered bytes next to the transcript, for diagnosing the renderer."""
    with tempfile.NamedTemporaryFile(mode="wb", delete=False, suffix=".script_output.raw") as raw_file:
        raw_file.write(raw_output)
        console.debug(f"{script_type} script raw output stored in: {raw_file.name}", color=MUTED_COLOR)


def _publish_exit(
    script: str,
    script_type: str,
    exit_code: int,
    output: str,
    elapsed_time: float,
    frid: Optional[str],
    module: Optional[str],
) -> tuple[int, str, Optional[str]]:
    with tempfile.NamedTemporaryFile(mode="w+", encoding="utf-8", delete=False, suffix=".script_output") as temp_file:
        temp_file.write(f"\n═════════════════════════ {script_type} Script Output ═════════════════════════\n")
        temp_file.write(output)
        temp_file.write("\n══════════════════════════════════════════════════════════════════════\n")
        temp_file_path = temp_file.name
        if exit_code != 0:
            temp_file.write(f"{script_type} script {script} failed with exit code {exit_code}.\n")
        else:
            temp_file.write(f"{script_type} script {script} successfully passed.\n")
        temp_file.write(f"{script_type} script execution time: {elapsed_time:.2f} seconds.\n")

    console.debug(f"{script_type} script output stored in: {temp_file_path.strip()}", color=MUTED_COLOR)

    if exit_code != 0:
        if frid is not None:
            console.info(
                f"↻ The {script_type} script for functionality ID {frid} of module {module} has failed. "
                f"Initiating the patching mode to automatically correct the discrepancies.",
                color=RETRY_COLOR,
            )
        else:
            console.info(
                f"↻ The {script_type} script has failed. "
                f"Initiating the patching mode to automatically correct the discrepancies.",
                color=RETRY_COLOR,
            )
    else:
        if frid is not None:
            console.info(
                f"✓ The {script_type} script for functionality ID {frid} of module {module} "
                f"has passed successfully.",
                color=SUCCESS_COLOR,
            )
        else:
            console.info(f"✓ All {script_type} scripts have passed successfully.", color=SUCCESS_COLOR)

    return exit_code, output, temp_file_path


def _publish_environment_error(
    script: str, script_type: str, detail: str, output: str
) -> tuple[int, str, Optional[str]]:
    """The 69 channel: an infrastructure failure is never handed to the patcher."""
    issue = f"{script_type} script {script} could not be executed: {detail}"
    with tempfile.NamedTemporaryFile(mode="w+", encoding="utf-8", delete=False, suffix=".script_output") as temp_file:
        temp_file.write(f"{issue}\n")
        if output:
            temp_file.write(f"{script_type} script output before the failure:\n{output}")
        temp_file_path = temp_file.name
    console.warning(f"{issue} {script_type} script output stored in: {temp_file_path}")
    if output:
        issue = f"{issue}\nPartial {script_type} script output:\n{output}"
    return ENVIRONMENT_ERROR_EXIT_CODE, issue, temp_file_path


def _publish_timeout(
    script: str,
    script_type: str,
    script_timeout: float,
    output: str,
    reply_failed: bool,
    reply_detail: str,
) -> tuple[int, str, Optional[str]]:
    diagnostics = NO_INPUT_DIAGNOSTIC if INPUT_DRIVER is None else ""
    if reply_failed:
        diagnostics += f" Terminal replies the script asked for could not be delivered: {reply_detail}."

    with tempfile.NamedTemporaryFile(mode="w+", encoding="utf-8", delete=False, suffix=".script_timeout") as temp_file:
        temp_file.write(f"{script_type} script {script} timed out after {script_timeout} seconds.")
        temp_file.write(diagnostics)
        if output:
            temp_file.write(f"{script_type} script partial output before the timeout:\n{output}")
        else:
            temp_file.write(f"{script_type} script did not produce any output before the timeout.")
        temp_file_path = temp_file.name
    console.warning(
        f"The {script_type} script timed out after {script_timeout} seconds.{diagnostics} "
        f"{script_type} script output stored in: {temp_file_path}"
    )

    partial_output = f"\nPartial test script output:\n{output}" if output else ""
    return (
        TIMEOUT_ERROR_EXIT_CODE,
        f"{script_type} script did not finish in {script_timeout} seconds.{diagnostics}{partial_output}",
        temp_file_path,
    )


def execute_script(
    script: str,
    scripts_args: list[str],
    script_type: str,
    frid: Optional[str] = None,
    module: Optional[str] = None,
    timeout: Optional[int] = None,
    stop_event: Optional[threading.Event] = None,
) -> tuple[int, str, Optional[str]]:
    script_timeout = timeout if timeout is not None else SCRIPT_EXECUTION_TIMEOUT

    script_path = file_utils.add_current_path_if_no_path(script)
    if sys.platform == "win32":
        if not script_path.lower().endswith(".ps1"):
            raise ValueError(f"On Windows, only PowerShell (.ps1) scripts are supported, but got: {script_path}")
        cmd = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", script_path] + scripts_args
    else:
        cmd = [script_path] + scripts_args

    start_time = time.time()
    execution = _run_script(cmd, script_timeout, stop_event)
    elapsed_time = time.time() - start_time
    outcome = execution.outcome
    _store_raw_output(script_type, execution.raw_output)

    # The outcome arbiter, in precedence order.
    if outcome.condition == CONDITION_INFRASTRUCTURE:
        return _publish_environment_error(script, script_type, outcome.detail, execution.output)
    if outcome.condition == CONDITION_CANCELLED:
        raise RenderCancelledError()
    if outcome.condition == CONDITION_TIMEOUT:
        return _publish_timeout(
            script, script_type, script_timeout, execution.output, execution.reply_failed, execution.reply_detail
        )
    if outcome.exit_code is None:
        return _publish_environment_error(
            script, script_type, "the script's exit status was never observed", execution.output
        )
    if execution.reply_failed:
        # The pumps were healthy and the script exited normally, but a reply it was
        # waiting for never reached it — so its exit status describes a run that did not
        # get the terminal it asked for.
        return _publish_environment_error(
            script,
            script_type,
            f"terminal replies the script asked for could not be delivered: {execution.reply_detail}",
            execution.output,
        )
    return _publish_exit(script, script_type, outcome.exit_code, execution.output, elapsed_time, frid, module)
