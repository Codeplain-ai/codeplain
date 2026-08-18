"""Render completion summary display."""

import logging
from typing import Optional

import plain2code_logger
from plain2code_console import console
from plain2code_state import RunState
from usage_summary import format_usage_summary

logger = logging.getLogger(plain2code_logger.LOGGER_NAME)

# Marks the last line of a render's log file. Greppable on purpose: benchmark runs and
# support artifacts are read by tooling before they are read by a person.
RENDER_TRAILER_PREFIX = "[render-trailer]"


def print_exit_summary(
    run_state: RunState,
    spec_filename: str,
    error_message: Optional[str] = None,
) -> None:
    """Print render outcome after the TUI exits (terminal restored)."""
    console.quiet = False

    if run_state.render_succeeded:
        msg = "\n[#79FC96]✓ rendering completed\n\n"
    elif run_state.render_cancelled:
        msg = "\n[#FFFFFF]— rendering canceled\n\n"
    else:
        msg = "\n[#FF6B6B]✗ rendering failed\n\n"
    msg += f"  [#8E8F91]render id:\t\t\t[#FFFFFF]{run_state.render_id}\n"
    msg += f"  [#8E8F91]input file:\t\t\t[#FFFFFF]{spec_filename}\n"
    msg += f"  [#8E8F91]generated code folder:\t[#FFFFFF]{run_state.render_generated_code_path or '-'}\n\n"
    msg += format_usage_summary(run_state.rendered_functionalities, run_state.render_time_accumulated) + "\n"
    console.print(msg)

    if not run_state.render_succeeded and error_message:
        console.error(error_message)
    console.quiet = True

    log_render_trailer(run_state, spec_filename, error_message)


def log_render_trailer(
    run_state: RunState,
    spec_filename: str,
    error_message: Optional[str] = None,
) -> None:
    """Writes the render's outcome to the log file, as its last line.

    The summary above reaches the terminal through Rich, which never touches logging, so
    a captured `codeplain.log` used to stop at whatever happened to be logged last —
    indistinguishable from a process that died silently. This ends every log with what
    the render did, and because it is written on every exit path a log *without* a
    trailer is itself evidence that the file was truncated.
    """
    if run_state.render_succeeded:
        outcome = "completed"
    elif run_state.render_cancelled:
        outcome = "cancelled"
    else:
        outcome = "failed"

    logger.info(
        f"{RENDER_TRAILER_PREFIX} outcome={outcome} "
        f"render_id={run_state.render_id} "
        f"functionalities={run_state.rendered_functionalities} "
        f"render_time_s={run_state.render_time_accumulated} "
        f"generated_code={run_state.render_generated_code_path or '-'} "
        f"spec={spec_filename}"
    )
    if outcome == "failed" and error_message:
        logger.error(f"{RENDER_TRAILER_PREFIX} error={error_message}")

    # The process may exit immediately after this; an unflushed trailer would defeat the
    # purpose of writing one.
    for handler in logger.handlers:
        handler.flush()
