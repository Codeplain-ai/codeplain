import time
from enum import Enum
from typing import Optional

from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.widgets import Button, Static

from .models import Substate
from .spinner import Spinner


class CustomFooter(Horizontal):
    """A custom footer with keyboard shortcuts and render ID."""

    NORMAL_TEXT = "ctrl+c: copy/quit  *  ctrl+l: toggle logs"
    QUIT_PENDING_TEXT = "Press ctrl+c again to quit  *  esc: cancel"

    def __init__(self, render_id: str = "", **kwargs):
        super().__init__(**kwargs)
        self.render_id = render_id
        self._footer_text_widget: Optional[Static] = None

    def compose(self):
        self._footer_text_widget = Static(self.NORMAL_TEXT, classes="custom-footer-text")
        yield self._footer_text_widget
        if self.render_id:
            yield Static(f"render id: {self.render_id}", classes="custom-footer-render-id")

    def update_quit_state(self, quit_pending: bool) -> None:
        """Update footer text based on quit-pending state."""
        if self._footer_text_widget is None:
            return
        if quit_pending:
            self._footer_text_widget.update(self.QUIT_PENDING_TEXT)
            self._footer_text_widget.remove_class("custom-footer-text")
            self._footer_text_widget.add_class("custom-footer-quit-pending")
        else:
            self._footer_text_widget.update(self.NORMAL_TEXT)
            self._footer_text_widget.remove_class("custom-footer-quit-pending")
            self._footer_text_widget.add_class("custom-footer-text")


class ScriptOutputType(str, Enum):
    UNIT_TEST_OUTPUT_TEXT = "Unit tests output: "
    CONFORMANCE_TEST_OUTPUT_TEXT = "Conformance tests output: "
    TESTING_ENVIRONMENT_OUTPUT_TEXT = "Testing environment preparation execution output: "

    @staticmethod
    def get_max_label_width(active_types: list["ScriptOutputType"]) -> int:
        """Get the maximum width of the active script output labels.

        Args:
            active_types: List of ScriptOutputType enum members that are currently active

        Returns:
            Maximum width among the active label types
        """
        if not active_types:
            return 0
        return max(len(script_type.value) for script_type in active_types)

    def get_padded_label(self) -> str:
        """Get the label left-aligned (no padding).

        Returns:
            Label without padding (left-aligned)
        """
        # Return label as-is without padding for left alignment
        return self.value


class TUIComponents(str, Enum):
    RENDER_MODULE_NAME_WIDGET = "render-module-name-widget"
    RENDER_ID_WIDGET = "render-id-widget"
    RENDER_STATUS_WIDGET = "render-status-widget"
    UNIT_TEST_SCRIPT_OUTPUT_WIDGET = "unit-test-script-output-widget"
    CONFORMANCE_TESTS_SCRIPT_OUTPUT_WIDGET = "conformance-tests-script-output-widget"
    TESTING_ENVIRONMENT_SCRIPT_OUTPUT_WIDGET = "testing-environment-script-output-widget"

    # FRID Progress widgets
    FRID_PROGRESS = "frid-progress"
    FRID_PROGRESS_HEADER = "frid-progress-header"
    FRID_PROGRESS_RENDER_FR = "frid-progress-render-fr"
    FRID_PROGRESS_UNIT_TEST = "frid-progress-unit-test"
    FRID_PROGRESS_REFACTORING = "frid-progress-refactoring"
    FRID_PROGRESS_CONFORMANCE_TEST = "frid-progress-conformance-test"

    # Test scripts container widgets
    TEST_SCRIPTS_CONTAINER = "test-scripts-container"

    CONTENT_SWITCHER = "content-switcher"
    DASHBOARD_VIEW = "dashboard-view"
    LOG_VIEW = "log-view"
    LOG_WIDGET = "log-widget"
    LOG_FILTER = "log-filter"


class SubstateLine(Horizontal):
    """A single substate row with an attached timer."""

    def __init__(self, text: str, indent: str, **kwargs):
        super().__init__(**kwargs)
        self.text = text
        self.indent = indent
        self.start_time = time.monotonic()
        self._line_widget: Static | None = None

    def compose(self):
        self._line_widget = Static(self._format_line(), classes="substate-line-text")
        yield self._line_widget

    def on_mount(self) -> None:
        self._refresh_timer()
        self.set_interval(1, self._refresh_timer)

    def _format_timer(self) -> str:
        elapsed = int(time.monotonic() - self.start_time)
        if elapsed < 60:
            return f"{elapsed}s"
        minutes = elapsed // 60
        seconds = elapsed % 60
        if minutes < 60:
            return f"{minutes}m {seconds}s"
        hours = minutes // 60
        return f"{hours}h {minutes % 60}m"

    def _format_line(self) -> str:
        timer = self._format_timer()
        return f"{self.indent}  └ {self.text} [#888888]({timer})[/#888888]"

    def _refresh_timer(self) -> None:
        try:
            if self._line_widget:
                self._line_widget.update(self._format_line())
        except Exception:
            pass


class ProgressItem(Vertical):
    """A vertical container for a status, description, and substates."""

    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    STOPPED = "STOPPED"

    def __init__(self, initial_text: str, **kwargs):
        super().__init__(**kwargs)
        self.initial_text = initial_text

    def compose(self):
        # Main row with status and description
        with Horizontal(id=f"{self.id}-main-row", classes="progress-main-row"):
            yield Static(self._get_status_text(self.PENDING), classes=f"status {self.PENDING}")
            yield Static(self.initial_text, classes="description")
        # Substates container (full width, aligned to left)
        yield Vertical(id=f"{self.id}-substates", classes="substates-container")

    def _get_status_text(self, status: str) -> str:
        """Get the display text for a given status."""
        if status == self.COMPLETED:
            return "✓ completed"
        elif status == self.PROCESSING:
            return "◉ processing"
        elif status == self.STOPPED:
            return "◼ stopped"
        else:
            return "○ pending"

    async def update_status(self, status: str):
        # TODO: Move to plain2code_tui.py
        try:
            # Get the main row container
            main_row = self.query_one(f"#{self.id}-main-row", Horizontal)

            # Remove existing status widget
            try:
                old_status = main_row.query_one(".status")
                await old_status.remove()
            except Exception:
                pass

            # Add appropriate widget based on status
            if status == self.PROCESSING:
                # Use spinner for processing state
                spinner = Spinner(text="processing", classes=f"status {status}")
                await main_row.mount(spinner, before=0)
            else:
                # Use static text for pending/completed
                status_widget = Static(self._get_status_text(status), classes=f"status {status}")
                await main_row.mount(status_widget, before=0)

        except Exception:
            pass

    def update_text(self, text: str):
        try:
            self.query_one(".description", Static).update(text)
        except Exception:
            pass

    async def set_substates(self, substates: list[Substate]):
        """Set multiple substates to display as a nested checklist.

        Args:
            substates: List of Substate objects to display (supports nesting up to 4 levels)
        """
        try:
            substates_container = self.query_one(f"#{self.id}-substates", Vertical)
            # Clear existing substates
            await substates_container.remove_children()

            # Render substates recursively
            await self._render_substates_recursive(substates_container, substates, depth=0)

            # Add a newline after all substates for visual separation
            if substates:  # Only add newline if there are substates
                newline_widget = Static("", classes="substate-separator")
                await substates_container.mount(newline_widget)
        except Exception:
            pass

    async def _render_substates_recursive(self, container: Vertical, substates: list[Substate], depth: int):
        """Recursively render substates with proper indentation.

        Args:
            container: The container to mount substates into
            substates: List of Substate objects to render
            depth: Current nesting depth (0-based, max 3 for 4 total levels)
        """
        indent = "    " * depth  # 4 spaces per level

        for substate in substates:
            # Render the current substate
            substate_widget = SubstateLine(substate.text, indent, classes="substate-row")
            await container.mount(substate_widget)

            # Recursively render children if they exist
            if substate.children:
                await self._render_substates_recursive(container, substate.children, depth + 1)

    async def clear_substates(self):
        """Clear all substates."""
        try:
            substates_container = self.query_one(f"#{self.id}-substates", Vertical)
            await substates_container.remove_children()
        except Exception:
            pass


class RenderingInfoBox(Vertical):
    """Responsive container for module and functionality information."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.module_text = ""
        self.functionality_text = ""
        self.module_widget: Static | None = None
        self.functionality_widget: Static | None = None

    def update_module(self, text: str) -> None:
        """Update the module name display."""
        self.module_text = text
        self._refresh_content()

    def update_functionality(self, text: str) -> None:
        """Update the functionality text display."""
        self.functionality_text = text
        self._refresh_content()

    def _refresh_content(self) -> None:
        """Refresh text inside the box."""
        if self.module_widget is not None:
            self.module_widget.update(self.module_text or "")
        if self.functionality_widget is not None:
            self.functionality_widget.update(self.functionality_text or "")

    def on_mount(self) -> None:
        """Initialize default labels on mount."""
        self.module_text = "Module: "
        self.functionality_text = "Functionality:"
        self._refresh_content()

    def compose(self):
        self.module_widget = Static(self.module_text, classes="rendering-info-row")
        self.functionality_widget = Static(self.functionality_text, classes="rendering-info-row")
        yield Static("module status", classes="rendering-info-title")
        with Vertical(classes="rendering-info-box"):
            yield self.module_widget
            yield self.functionality_widget


class TestScriptsContainer(Vertical):
    """Container with ASCII border for test script outputs."""

    def __init__(
        self,
        show_unit_test: bool = True,
        show_conformance_test: bool = True,
        show_testing_env: bool = True,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.show_unit_test = show_unit_test
        self.show_conformance_test = show_conformance_test
        self.show_testing_env = show_testing_env
        self.unit_test_text = ScriptOutputType.UNIT_TEST_OUTPUT_TEXT.value
        self.conformance_test_text = ScriptOutputType.CONFORMANCE_TEST_OUTPUT_TEXT.value
        self.testing_env_text = ScriptOutputType.TESTING_ENVIRONMENT_OUTPUT_TEXT.value
        self.unit_widget: Static | None = None
        self.conformance_widget: Static | None = None
        self.testing_widget: Static | None = None

    def update_unit_test(self, text: str) -> None:
        """Update unit test output and refresh."""
        self.unit_test_text = text
        self._refresh_content()

    def update_conformance_test(self, text: str) -> None:
        """Update conformance test output and refresh."""
        self.conformance_test_text = text
        self._refresh_content()

    def update_testing_env(self, text: str) -> None:
        """Update testing env output and refresh."""
        self.testing_env_text = text
        self._refresh_content()

    def _refresh_content(self) -> None:
        """Refresh the test script rows."""
        if self.unit_widget is not None:
            self.unit_widget.update(self.unit_test_text)
            self.unit_widget.display = self.show_unit_test
        if self.conformance_widget is not None:
            self.conformance_widget.update(self.conformance_test_text)
            self.conformance_widget.display = self.show_conformance_test
        if self.testing_widget is not None:
            self.testing_widget.update(self.testing_env_text)
            self.testing_widget.display = self.show_testing_env

    def on_mount(self) -> None:
        """Initialize the box on mount."""
        self._refresh_content()

    def compose(self):
        yield Static("testing status", classes="test-scripts-title")
        with Vertical(classes="test-scripts-box"):
            self.unit_widget = Static(self.unit_test_text, classes="test-script-row")
            self.conformance_widget = Static(self.conformance_test_text, classes="test-script-row")
            self.testing_widget = Static(self.testing_env_text, classes="test-script-row")
            yield self.unit_widget
            yield self.conformance_widget
            yield self.testing_widget


class FRIDProgress(Vertical):
    """A widget to display the status of subcomponent tasks."""

    # Display text for progress items (UI-specific)
    IMPLEMENTING_FUNCTIONALITY_TEXT = "Implementing the functionality"
    UNIT_TEST_VALIDATION_TEXT = "Unit tests"
    REFACTORING_TEXT = "Refactoring"
    CONFORMANCE_TEST_VALIDATION_TEXT = "Conformance tests"

    RENDERING_MODULE_TEXT = "Module: "
    RENDERING_FUNCTIONALITY_TEXT = "Functionality:"

    def __init__(
        self,
        unittests_script: Optional[str],
        conformance_tests_script: Optional[str],
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.unittests_script = unittests_script
        self.conformance_tests_script = conformance_tests_script

    def update_fr_text(self, text: str) -> None:
        try:
            # Update the rendering info box instead
            info_box = self.query_one(RenderingInfoBox)
            info_box.update_functionality(text)
        except Exception:
            pass

    def update_fr_status(self, status: str) -> None:
        try:
            widget = self.query_one(f"#{TUIComponents.FRID_PROGRESS_RENDER_FR.value}", ProgressItem)
            self.call_later(widget.update_status, status)
        except Exception:
            pass

    def on_mount(self) -> None:
        self.border_title = "FRID Progress"

    def compose(self):
        yield RenderingInfoBox()
        yield Static("rendering status", classes="frid-state-machine-title")
        with Vertical(classes="frid-state-machine-box"):
            yield ProgressItem(
                self.IMPLEMENTING_FUNCTIONALITY_TEXT,
                id=TUIComponents.FRID_PROGRESS_RENDER_FR.value,
            )
            if self.unittests_script is not None:
                yield ProgressItem(
                    self.UNIT_TEST_VALIDATION_TEXT,
                    id=TUIComponents.FRID_PROGRESS_UNIT_TEST.value,
                )
            yield ProgressItem(
                self.REFACTORING_TEXT,
                id=TUIComponents.FRID_PROGRESS_REFACTORING.value,
            )
            if self.conformance_tests_script is not None:
                yield ProgressItem(
                    self.CONFORMANCE_TEST_VALIDATION_TEXT,
                    id=TUIComponents.FRID_PROGRESS_CONFORMANCE_TEST.value,
                )


class LogEntry(Vertical):
    """A single log entry that can be expanded to show details."""

    def __init__(self, logger_name: str, level: str, message: str, timestamp: str = "", **kwargs):
        super().__init__(**kwargs)
        self.logger_name = logger_name
        self.level = level
        self.message = message
        self.timestamp = timestamp
        self.is_expanded = False
        self.classes = f"log-entry log-{level.lower()}"

    def compose(self):
        # Main row: just the message with a clickable indicator
        with Horizontal(classes="log-main-row"):
            # Expandable indicator
            yield Static("▶", classes="log-expand-indicator")

            time_part = self.timestamp.split()[-1] if self.timestamp else ""
            time_prefix = f"[#888888][{time_part}][/#888888] " if time_part else ""
            indent_spaces = len(f"[{time_part}] ") if time_part else 0

            message_body = self.message
            if any(
                keyword in self.message.lower()
                for keyword in ["completed", "success", "successfully", "passed", "done", "✓"]
            ):
                message_body = f"[green]✓[/green] {message_body}"

            if indent_spaces and "\n" in message_body:
                message_body = message_body.replace("\n", "\n" + " " * indent_spaces)

            yield Static(f"{time_prefix}{message_body}", classes="log-col-message")

        # Details row (hidden by default) - vertical layout
        with Vertical(id=f"log-details-{id(self)}", classes="log-details-row"):
            location = self.logger_name
            if len(location) > 20:
                location = location[:20] + "..."
            yield Static(f"  [#888]level:[/#888] {self.level}", classes="log-details-text")
            yield Static(f"  [#888]location:[/#888] {location}", classes="log-details-text")

    def on_mount(self) -> None:
        """Hide details on mount."""
        try:
            details = self.query_one(f"#log-details-{id(self)}")
            details.display = False
        except Exception:
            pass

    def on_click(self) -> None:
        """Toggle details visibility on click."""
        try:
            details = self.query_one(f"#log-details-{id(self)}")
            indicator = self.query_one(".log-expand-indicator", Static)

            self.is_expanded = not self.is_expanded
            details.display = self.is_expanded
            indicator.update("▼" if self.is_expanded else "▶")
        except Exception:
            pass


class StructuredLogView(VerticalScroll):
    """A scrollable container for log entries displayed as a table."""

    # Log level hierarchy (lower number = lower priority)
    LOG_LEVELS = {
        "DEBUG": 0,
        "INFO": 1,
        "WARNING": 2,
        "ERROR": 3,
    }

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.min_level = "DEBUG"  # Show all by default

    def _should_show_log(self, level: str) -> bool:
        """Check if log should be shown based on minimum level."""
        log_priority = self.LOG_LEVELS.get(level, 0)
        min_priority = self.LOG_LEVELS.get(self.min_level, 0)
        return log_priority >= min_priority

    async def add_log(self, logger_name: str, level: str, message: str, timestamp: str = ""):
        """Add a new log entry."""
        # Check if this is a success message that should have spacing before it
        is_success_message = any(
            keyword in message.lower() for keyword in ["completed", "success", "successfully", "passed", "done", "✓"]
        )

        # Add empty line before success messages
        if is_success_message:
            spacer = Static("", classes="log-spacer")
            await self.mount(spacer)

        entry = LogEntry(logger_name, level, message, timestamp)

        # Only show if level is >= minimum level
        if not self._should_show_log(level):
            entry.display = False

        await self.mount(entry)
        # Auto-scroll to bottom to show latest logs
        self.scroll_end(animate=False)

    def filter_logs(self, min_level: str):
        """Show/hide logs based on minimum level."""
        self.min_level = min_level

        # Update visibility of all existing log entries
        for entry in self.query(LogEntry):
            entry.display = self._should_show_log(entry.level)


class LogFilterChanged(Message):
    """Message sent when log filter changes."""

    def __init__(self, min_level: str):
        super().__init__()
        self.min_level = min_level


class LogLevelFilter(Horizontal):
    """Filter logs by minimum level with buttons."""

    LEVELS = ["debug", "info", "warning", "error"]

    # Make the widget focusable to receive keyboard events
    can_focus = True

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.current_level = "INFO"
        self.current_index = self.LEVELS.index(self.current_level.lower())

    def compose(self):
        yield Static("level: ", classes="filter-label")
        with Horizontal(classes="filter-buttons-container"):
            for level in self.LEVELS:
                variant = "primary" if level.upper() == self.current_level else "default"
                btn = Button(level.upper(), id=f"filter-{level.lower()}", variant=variant, classes="filter-button")  # type: ignore[arg-type]
                btn.can_focus = False  # Prevent buttons from receiving focus
                yield btn

    def on_key(self, event):
        """Handle tab key to cycle through levels."""
        if event.key == "tab":
            # Move to next level
            self.current_index = (self.current_index + 1) % len(self.LEVELS)
            new_level = self.LEVELS[self.current_index].upper()
            self._update_level(new_level)
            event.prevent_default()
            event.stop()

    def on_button_pressed(self, event):
        """Handle level button press."""
        # Extract level from button ID
        button_id = event.button.id
        if button_id and button_id.startswith("filter-"):
            level = button_id.replace("filter-", "").upper()
            self.current_index = self.LEVELS.index(level.lower())
            self._update_level(level)

    def _update_level(self, level: str):
        """Update the current level and button states."""
        self.current_level = level

        # Update button variants
        for btn in self.query(Button):
            if btn.id == f"filter-{level.lower()}":
                btn.variant = "primary"
            else:
                btn.variant = "default"
            btn.refresh()  # Force immediate visual update

        # Notify parent to refresh log visibility
        self.post_message(LogFilterChanged(level))
