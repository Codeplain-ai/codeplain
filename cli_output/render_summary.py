"""Render completion summary display."""

from typing import Optional

from plain2code_console import console
from plain2code_state import RunState
from plain2code_utils import format_duration_hms


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
    msg += f"[#8E8F91]functionalities  [#FFFFFF]{run_state.rendered_functionalities}  [#8E8F91]used credits  [#FFFFFF]{run_state.rendered_functionalities}  [#8E8F91]render time  [#FFFFFF]{format_duration_hms(run_state.render_time_accumulated)}\n"
    console.print(msg)

    if not run_state.render_succeeded and error_message:
        console.error(error_message)
    console.quiet = True
