"""Widget update helper utilities for Plain2Code TUI."""

from datetime import datetime

from textual.css.query import NoMatches
from textual.widgets import Static

from usage_summary import format_usage_summary

from .components import FRIDProgress, ProgressItem, RenderingInfoBox, StructuredLogView, SubstateLine, TUIComponents
from .models import Substate


async def _async_update_status(widget: ProgressItem, status: str) -> None:
    """Async helper to update widget status."""
    await widget.update_status(status)


async def _async_set_substates(widget: ProgressItem, substates: list[Substate]) -> None:
    """Async helper to set widget substates."""
    await widget.set_substates(substates)


async def _async_clear_substates(widget: ProgressItem) -> None:
    """Async helper to clear widget substates."""
    await widget.clear_substates()


def log_to_widget(tui, level: str, message: str) -> None:
    """Helper to log messages to the TUI log widget.

    Args:
        tui: The Plain2CodeTUI instance
        level: Log level (e.g., "WARNING", "ERROR")
        message: The log message
    """
    try:
        log_widget = tui.query_one(f"#{TUIComponents.LOG_WIDGET.value}", StructuredLogView)
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        tui.call_later(
            log_widget.add_log,
            "tui.widget_helpers",
            level,
            message,
            timestamp,
        )
    except Exception:
        # Silently fail if log widget is not available
        pass


def update_progress_item_status(tui, widget_id: str, status: str) -> None:
    """Helper function to safely update a ProgressItem's status.

    Args:
        tui: The Plain2CodeTUI instance
        widget_id: The widget ID to update
        status: The new status value
    """
    try:
        widget = tui.query_one(f"#{widget_id}", ProgressItem)
        tui.call_later(_async_update_status, widget, status)
    except NoMatches as e:
        log_to_widget(tui, "WARNING", f"ProgressItem {widget_id} not found: {e}")
    except Exception as e:
        log_to_widget(tui, "ERROR", f"Error updating progress item {widget_id}: {e}")

    if status == ProgressItem.COMPLETED:
        clear_progress_item_substates(tui, widget_id)


def get_frid_progress(tui) -> FRIDProgress:
    """Helper function to safely get the FRIDProgress widget.

    Args:
        tui: The Plain2CodeTUI instance

    Returns:
        The FRIDProgress widget instance
    """
    return tui.query_one(f"#{TUIComponents.FRID_PROGRESS.value}", FRIDProgress)


def display_success_message(tui, rendered_code_path: str):
    """Display success message with code location and exit instructions.

    Args:
        tui: The Plain2CodeTUI instance
        rendered_code_path: The path to the rendered code
    """

    message = (
        f"[#79FC96]✓ rendering completed![/#79FC96] [#888888](press enter to exit)[/#888888]\n"
        f"[#888888]generated code folder: {rendered_code_path}[/#888888] "
    )

    widget: Static = tui.query_one(f"#{TUIComponents.RENDER_STATUS_WIDGET.value}", Static)
    widget.update(message)


FRID_PROGRESS_IDS = [
    TUIComponents.FRID_PROGRESS_RENDER_FR.value,
    TUIComponents.FRID_PROGRESS_UNIT_TEST.value,
    TUIComponents.FRID_PROGRESS_REFACTORING.value,
    TUIComponents.FRID_PROGRESS_CONFORMANCE_TEST.value,
]


def transition_frid_progress(tui, from_status: str | None, to_status: str):
    """Transition all FRID progress items matching from_status to to_status."""
    for widget_id in FRID_PROGRESS_IDS:
        try:
            widget = tui.query_one(f"#{widget_id}", ProgressItem)
            if widget.current_status == from_status or from_status is None:
                update_progress_item_status(tui, widget_id, to_status)
        except Exception:
            pass


def display_error_message(tui, error_message: str):
    widget: Static = tui.query_one(f"#{TUIComponents.RENDER_STATUS_WIDGET.value}", Static)
    widget.add_class("error")
    widget.update(error_message)


def display_usage_summary(tui, functionalities: int, render_time_seconds: float) -> None:
    """Update the credit-usage line beneath the render-status widget.

    Shows how many functionalities were rendered, the credits they consumed
    (one per functionality), and the render time so far. Fails silently if the
    widget is not mounted (e.g. during teardown).
    """
    try:
        widget: Static = tui.query_one(f"#{TUIComponents.RENDER_USAGE_WIDGET.value}", Static)
        widget.update(format_usage_summary(functionalities, render_time_seconds))
    except NoMatches:
        pass


def update_progress_item_substates(tui, widget_id: str, substates: list[Substate]) -> None:
    """Helper function to safely set substates for a ProgressItem.

    Args:
        tui: The Plain2CodeTUI instance
        widget_id: The widget ID to update
        substates: List of Substate objects to display (supports nesting up to 4 levels)
    """
    try:
        widget = tui.query_one(f"#{widget_id}", ProgressItem)
        tui.call_later(_async_set_substates, widget, substates)
    except NoMatches as e:
        log_to_widget(tui, "WARNING", f"ProgressItem {widget_id} not found: {e}")
    except Exception as e:
        log_to_widget(tui, "ERROR", f"Error updating substates for {widget_id}: {e}")


def clear_progress_item_substates(tui, widget_id: str) -> None:
    """Helper function to safely clear substates for a ProgressItem.

    Args:
        tui: The Plain2CodeTUI instance
        widget_id: The widget ID to update
    """
    try:
        widget = tui.query_one(f"#{widget_id}", ProgressItem)
        tui.call_later(_async_clear_substates, widget)
    except NoMatches as e:
        log_to_widget(tui, "WARNING", f"ProgressItem {widget_id} not found: {e}")
    except Exception as e:
        log_to_widget(tui, "ERROR", f"Error clearing substates for {widget_id}: {e}")


def display_module_name(tui, module_name: str):
    """Helper function to display the module name in the FRIDProgress widget.

    Args:
        tui: The Plain2CodeTUI instance
        module_name: The module name to display
    """
    frid_progress = get_frid_progress(tui)
    info_box = frid_progress.query_one(RenderingInfoBox)
    info_box.update_module(f"{FRIDProgress.RENDERING_MODULE_TEXT}{module_name}")


def stop_progress_timer(tui):
    """Helper function to stop the progress timer in the FRIDProgress widget.

    Args:
        tui: The Plain2CodeTUI instance
    """
    frid_progress = get_frid_progress(tui)
    substate_line = frid_progress.query_one(SubstateLine)
    substate_line.stop_progress_timer()
