"""Shared rendering of the credit-usage summary line.

The same 'functionalities / used credits / render time' line is shown by the
non-interactive console summary (``cli_output``) and by the interactive TUI
(``tui``), so its wording and markup live in this neutral root module as a single
source of truth. It intentionally has no dependency on either presentation
package, which keeps the TUI independent of the renderer/console output layer.
"""

from plain2code_utils import format_duration_hms


def format_usage_summary(
    functionalities: int,
    render_time_seconds: float,
    label_color: str = "#8E8F91",
    value_color: str = "#FFFFFF",
) -> str:
    """Build the shared 'functionalities / used credits / render time' usage line.

    Used credits equals the number of rendered functionalities (one credit is
    charged per functional requirement). The returned string carries Rich markup
    and is consumed identically by the console summary and the TUI.
    """
    used_credits = functionalities
    return (
        f"[{label_color}]functionalities  [{value_color}]{functionalities}  "
        f"[{label_color}]used credits  [{value_color}]{used_credits}  "
        f"[{label_color}]render time  [{value_color}]{format_duration_hms(render_time_seconds)}"
    )
