import os
import threading
import time
from typing import Callable, Optional

from textual.app import App, ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.widgets import ContentSwitcher, Static
from textual.worker import Worker, WorkerFailed, WorkerState

from event_bus import EventBus
from plain2code_console import console
from plain2code_events import (
    LogMessageEmitted,
    RenderCompleted,
    RenderContextSnapshot,
    RenderFailed,
    RenderModuleCompleted,
    RenderModuleStarted,
    RenderStateUpdated,
)
from plain2code_exceptions import InternalServerError
from render_machine.states import States
from tui.widget_helpers import log_to_widget

from .components import (
    CustomFooter,
    FRIDProgress,
    LogFilterChanged,
    LogLevelFilter,
    RenderingInfoBox,
    ScriptOutputType,
    StructuredLogView,
    TestScriptsContainer,
    TUIComponents,
)
from .state_handlers import (
    ConformanceTestsHandler,
    FridFullyImplementedHandler,
    FridReadyHandler,
    RefactoringHandler,
    RenderErrorHandler,
    RenderSuccessHandler,
    ScriptOutputsHandler,
    StateCompletionHandler,
    StateHandler,
    UnitTestsHandler,
)

FORCE_EXIT_DELAY = 0.5  # seconds


class Plain2CodeTUI(App):
    """A Textual TUI for plain2code."""

    BINDINGS = [
        ("ctrl+c", "quit", "Quit"),
        ("ctrl+l", "toggle_logs", "Toggle Logs"),
    ]

    def __init__(
        self,
        event_bus: EventBus,
        worker_fun: Callable[[], None],
        render_id: str,
        unittests_script: str,
        conformance_tests_script: str,
        prepare_environment_script: str,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.dark = True  # Set dark mode as default
        self.event_bus = event_bus
        self.worker_fun = worker_fun
        self.render_id = render_id
        self.unittests_script: Optional[str] = unittests_script
        self.conformance_tests_script: Optional[str] = conformance_tests_script
        self.prepare_environment_script: Optional[str] = prepare_environment_script

        # Initialize state handlers
        self._state_handlers: dict[str, StateHandler] = {
            States.READY_FOR_FRID_IMPLEMENTATION.value: FridReadyHandler(
                self, self.unittests_script, self.conformance_tests_script
            ),
            States.PROCESSING_UNIT_TESTS.value: UnitTestsHandler(
                self, self.unittests_script, self.conformance_tests_script
            ),
            States.REFACTORING_CODE.value: RefactoringHandler(
                self, self.unittests_script, self.conformance_tests_script
            ),
            States.PROCESSING_CONFORMANCE_TESTS.value: ConformanceTestsHandler(
                self, self.unittests_script, self.conformance_tests_script
            ),
            States.FRID_FULLY_IMPLEMENTED.value: FridFullyImplementedHandler(
                self, self.unittests_script, self.conformance_tests_script
            ),
        }
        self._script_outputs_handler = ScriptOutputsHandler(self)
        self._render_error_handler = RenderErrorHandler(self)
        self._render_success_handler = RenderSuccessHandler(self)
        self._state_completion_handler = StateCompletionHandler(
            self, self.unittests_script, self.conformance_tests_script
        )

    def get_active_script_types(self) -> list[ScriptOutputType]:
        """Get the list of active script output types based on which scripts exist.

        Returns:
            List of ScriptOutputType enum members for scripts that are configured
        """
        active_types = []
        if self.unittests_script is not None:
            active_types.append(ScriptOutputType.UNIT_TEST_OUTPUT_TEXT)
        if self.conformance_tests_script is not None:
            active_types.append(ScriptOutputType.CONFORMANCE_TEST_OUTPUT_TEXT)
        if self.prepare_environment_script is not None:
            active_types.append(ScriptOutputType.TESTING_ENVIRONMENT_OUTPUT_TEXT)
        return active_types

    def on_mount(self) -> None:
        """Called when the app is mounted."""
        self.event_bus.register_main_thread_callback(self.call_from_thread)

        self.event_bus.subscribe(RenderStateUpdated, self.on_render_state_updated)
        self.event_bus.subscribe(RenderCompleted, self.on_render_completed)
        self.event_bus.subscribe(RenderFailed, self.on_render_failed)
        self.event_bus.subscribe(RenderModuleStarted, self.on_render_module_started)
        self.event_bus.subscribe(RenderModuleCompleted, self.on_render_module_completed)
        self.event_bus.subscribe(LogMessageEmitted, self.on_log_message_emitted)

        self.render_worker = self.run_worker(self.worker_fun, thread=True)

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        """Handle worker state changes."""
        if event.worker.state == WorkerState.ERROR:
            # Extract the original exception from WorkerFailed wrapper
            error = event.worker.error
            original_error = error.__cause__ if isinstance(error, WorkerFailed) and error.__cause__ else error

            # Every error in worker thread gets converted to InternalServerError so it's handled by the common call to
            # action in plain2code.py
            internal_error = InternalServerError(str(original_error))
            internal_error.__cause__ = original_error

            # Exit the TUI and return the wrapped exception
            self.exit(result=internal_error)

    def _handle_exception(self, error: Exception) -> None:
        """Override Textual's exception handler to suppress console tracebacks for worker errors.

        Worker exceptions are logged to file via the logging system (configured in file handler in plain2code.py),
        but the verbose Textual/Rich traceback is suppressed from the terminal. The clean error message
        is displayed via the exception handlers in main().
        """
        # Because TUI is running in main thread and code renderer is running in a worker thread, textual models this
        # by raising a WorkerFailed exception in this case
        if isinstance(error, WorkerFailed):
            # Here, we still print the error to get some additional information to the file, but it's probably
            # not necessary because we either way print the entire traceback to the console. Here, we could in the future
            # print more information if we would want to.
            original_error = error.__cause__ if error.__cause__ else error
            console.error(
                f"Worker failed with exception: {type(original_error).__name__}: {original_error}",
                exc_info=(type(original_error), original_error, original_error.__traceback__),
                stack_info=False,
            )
            return

        # For non-worker exceptions, use the default Textual behavior
        super()._handle_exception(error)

    def compose(self) -> ComposeResult:
        """Create child widgets for the app."""
        with ContentSwitcher(id=TUIComponents.CONTENT_SWITCHER.value, initial=TUIComponents.DASHBOARD_VIEW.value):
            with Vertical(id=TUIComponents.DASHBOARD_VIEW.value):
                with VerticalScroll():
                    yield Static(
                        "[#E0FF6E]*codeplain[/#E0FF6E] [#888888](v0.5.0)[/#888888]\n",
                        id="codeplain-header",
                        classes="codeplain-header",
                    )
                    yield Static(
                        "[#FFFFFF]Rendering in progress...[/#FFFFFF]",
                        id=TUIComponents.RENDER_STATUS_WIDGET.value,
                    )
                    yield FRIDProgress(
                        id=TUIComponents.FRID_PROGRESS.value,
                        unittests_script=self.unittests_script,
                        conformance_tests_script=self.conformance_tests_script,
                    )

                    # Test scripts container with border
                    yield TestScriptsContainer(
                        id=TUIComponents.TEST_SCRIPTS_CONTAINER.value,
                        show_unit_test=self.unittests_script is not None,
                        show_conformance_test=self.conformance_tests_script is not None,
                        show_testing_env=self.prepare_environment_script is not None,
                    )
            with Vertical(id=TUIComponents.LOG_VIEW.value):
                yield LogLevelFilter(id=TUIComponents.LOG_FILTER.value)
                yield Static("", classes="filter-spacer")
                yield StructuredLogView(id=TUIComponents.LOG_WIDGET.value)
        yield CustomFooter(render_id=self.render_id)

    def action_toggle_logs(self) -> None:
        """Toggle between dashboard and log view."""
        switcher = self.query_one(f"#{TUIComponents.CONTENT_SWITCHER.value}", ContentSwitcher)
        if switcher.current == TUIComponents.DASHBOARD_VIEW.value:
            switcher.current = TUIComponents.LOG_VIEW.value
        else:
            switcher.current = TUIComponents.DASHBOARD_VIEW.value

    def on_render_module_started(self, event: RenderModuleStarted):
        """Update TUI based on the current state machine state."""
        try:
            frid_progress = self.query_one(f"#{TUIComponents.FRID_PROGRESS.value}", FRIDProgress)
            info_box = frid_progress.query_one(RenderingInfoBox)
            info_box.update_module(f"{FRIDProgress.RENDERING_MODULE_TEXT}{event.module_name}")
        except Exception as e:
            log_to_widget(self, "WARNING", f"Error updating render module name: {type(e).__name__}: {e}")

    def on_render_module_completed(self, _event: RenderModuleCompleted):
        """Update TUI based on the current state machine state."""
        try:
            frid_progress = self.query_one(f"#{TUIComponents.FRID_PROGRESS.value}", FRIDProgress)
            info_box = frid_progress.query_one(RenderingInfoBox)
            info_box.update_module(FRIDProgress.RENDERING_MODULE_TEXT)
        except Exception as e:
            log_to_widget(self, "WARNING", f"Error resetting render module name: {type(e).__name__}: {e}")

    def on_log_message_emitted(self, event: LogMessageEmitted):
        try:
            log_widget = self.query_one(f"#{TUIComponents.LOG_WIDGET.value}", StructuredLogView)
            self.call_later(
                log_widget.add_log,
                event.logger_name,
                event.level,
                event.message,
                event.timestamp,
            )
        except Exception as e:
            log_to_widget(
                self, "WARNING", f"Error adding log message from {event.logger_name}: {type(e).__name__}: {e}"
            )

    def on_log_filter_changed(self, event: LogFilterChanged):
        """Handle log filter changes from LogLevelFilter widget."""
        try:
            log_widget = self.query_one(f"#{TUIComponents.LOG_WIDGET.value}", StructuredLogView)
            log_widget.filter_logs(event.min_level)
        except Exception as e:
            log_to_widget(self, "WARNING", f"Error filtering logs to level {event.min_level}: {type(e).__name__}: {e}")

    def on_render_state_updated(self, event: RenderStateUpdated):
        """Update TUI based on the current state machine state."""
        # 1. Parse current and previous state
        segments = event.state.split("_")
        if len(segments) < 2:
            return
        previous_state_segments = [] if event.previous_state is None else event.previous_state.split("_")

        # 3. Route to appropriate handler based on top-level state
        if segments[0] == States.IMPLEMENTING_FRID.value:
            self._handle_frid_state(segments, event.snapshot, previous_state_segments)

    def _handle_frid_state(
        self, segments: list[str], snapshot: RenderContextSnapshot, previous_state_segments: list[str]
    ) -> None:
        """Handle all states under IMPLEMENTING_FRID."""
        phase = segments[1]

        # Dispatch to appropriate handler
        handler = self._state_handlers.get(phase, None)
        if handler:
            handler.handle(segments, snapshot, previous_state_segments)
        if snapshot.script_execution_history.should_update_script_outputs:
            self._script_outputs_handler.handle(segments, snapshot, previous_state_segments)

        self._state_completion_handler.handle(segments, snapshot, previous_state_segments)

    def on_render_completed(self, _event: RenderCompleted):
        """Handle successful render completion."""
        self._render_success_handler.handle()

    def on_render_failed(self, event: RenderFailed):
        """Handle render failure."""
        self._render_error_handler.handle(event.error_message)

    def _ensure_exit(self) -> None:
        """Ensure exit the application immediately."""

        def ensure_exit():
            time.sleep(FORCE_EXIT_DELAY)
            try:
                if hasattr(self, "_driver") and self._driver is not None:
                    # suspend_application_mode() calls stop_application_mode() and close()
                    # It ensures terminal reset sequences are flushed before exiting.
                    self._driver.suspend_application_mode()
            except Exception as e:
                log_to_widget(self, "WARNING", f"Error suspending application mode: {type(e).__name__}: {e}")
            finally:
                os._exit(0)  # Kill process immediately, no cleanup - terminates all threads

        # daemon=True ensures this thread dies with the process if it exits before the timer fires
        threading.Thread(target=ensure_exit, daemon=True).start()

    def action_quit(self) -> None:
        """An action to quit the application immediately when user presses 'q'.

        Note: Force exit may leave files partially written if interrupted during file I/O operations.
        This is acceptable since the folders in which we are writing are git versioned and are reset in the next render.
        """
        # Show stopping message to user
        self.render_worker.cancel()
        self.exit()
