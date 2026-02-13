"""Widget update helper utilities for Plain2Code TUI."""

from datetime import datetime

from textual.css.query import NoMatches
from textual.widgets import Static

from .components import FRIDProgress, ProgressItem, StructuredLogView, TUIComponents
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


def update_widget_text(tui, widget_id: str, text: str) -> None:
    """Helper function to safely update a widget's text.

    Args:
        tui: The Plain2CodeTUI instance
        widget_id: The widget ID to update
        text: The new text value
    """
    try:
        widget = tui.query_one(f"#{widget_id}")
        if widget and hasattr(widget, "update"):
            widget.update(text)
    except NoMatches as e:
        log_to_widget(tui, "WARNING", f"Widget {widget_id} not found: {e}")
    except Exception as e:
        log_to_widget(tui, "ERROR", f"Error updating widget {widget_id}: {e}")


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

    message = f"[#79FC96]✓ Rendering finished![/#79FC96] [#888888](ctrl+d to exit)[/#888888]\n[#888888]Generated code: {rendered_code_path}[/#888888] "

    widget: Static = tui.query_one(f"#{TUIComponents.RENDER_STATUS_WIDGET.value}", Static)
    widget.update(message)


def set_frid_progress_to_stopped(tui):
    progress_ids = [
        TUIComponents.FRID_PROGRESS_RENDER_FR.value,
        TUIComponents.FRID_PROGRESS_UNIT_TEST.value,
        TUIComponents.FRID_PROGRESS_REFACTORING.value,
        TUIComponents.FRID_PROGRESS_CONFORMANCE_TEST.value,
    ]

    for widget_id in progress_ids:
        update_progress_item_status(tui, widget_id, ProgressItem.STOPPED)
        clear_progress_item_substates(tui, widget_id)


def display_error_message(tui, error_message: str):
    widget: Static = tui.query_one(f"#{TUIComponents.RENDER_STATUS_WIDGET.value}", Static)
    widget.add_class("error")
    widget.update(error_message)


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
