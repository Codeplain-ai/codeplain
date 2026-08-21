"""Render completion summary display."""

import logging
from typing import Optional

import plain2code_logger
from plain2code_console import console
from plain2code_state import RunState
from usage_summary import format_usage_summary

logger = logging.getLogger(plain2code_logger.LOGGER_NAME)

# Marks the last line of a render's log file. Greppable on purpose: these logs are read
# by tooling before they are read by a person.
RENDER_TRAILER_PREFIX = "[render-trailer]"


def render_outcome(run_state: RunState, error_message: Optional[str]) -> str:
    """What the render did, for the banner and the trailer alike.

    A reason recorded on the way out overrides the success flag. The flag is set per
    module, so an earlier module's success can still be standing while the process exits
    on an error - which read as a completed render that produced nothing.
    """
    if run_state.render_succeeded and not error_message:
        return "completed"
    if run_state.render_cancelled:
        return "cancelled"
    return "failed"


def print_exit_summary(
    run_state: RunState,
    spec_filename: str,
    error_message: Optional[str] = None,
) -> None:
    """Print render outcome after the TUI exits (terminal restored)."""
    console.quiet = False

    if render_outcome(run_state, error_message) == "completed":
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

    # Reported whenever there is one. A render can finish its functionalities and still
    # raise on the way out — publishing the build, for instance — and that combination
    # used to print the success banner and swallow the reason entirely, leaving a caller
    # with a tick mark and a non-zero exit code.
    if error_message:
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
    outcome = render_outcome(run_state, error_message)

    # Both times: an action that raises never reaches the render's own terminal accounting,
    # so the banked figure is stale on the paths this trailer exists to describe, and the
    # live one covers them without changing what render_time_s has always meant.
    trailer = (
        f"{RENDER_TRAILER_PREFIX} outcome={outcome} "
        f"render_id={run_state.render_id} "
        f"functionalities={run_state.rendered_functionalities} "
        f"render_time_s={run_state.render_time_accumulated} "
        f"render_time_live_s={run_state.get_live_render_time()} "
        f"generated_code={run_state.render_generated_code_path or '-'} "
        f"spec={spec_filename}"
    )
    if error_message:
        # On the same record, and quoted: a second record would leave the outcome one line
        # short of the end, and exception messages routinely carry newlines.
        trailer += f" error={error_message!r}"
    logger.info(trailer)

    # The process may exit immediately after this; an unflushed trailer would defeat the
    # purpose of writing one.
    for handler in logger.handlers:
        handler.flush()
