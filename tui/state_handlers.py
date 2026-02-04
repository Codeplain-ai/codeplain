"""State handlers for Plain2Code TUI state machine transitions."""

from abc import ABC, abstractmethod
from typing import Optional

from plain2code_events import RenderContextSnapshot
from render_machine.states import States

from . import components as tui_components
from .components import ProgressItem, ScriptOutputType, TestScriptsContainer, TUIComponents
from .models import Substate
from .widget_helpers import (
    display_error_message,
    display_success_message,
    get_frid_progress,
    set_frid_progress_to_stopped,
    update_progress_item_status,
    update_progress_item_substates,
)


def format_acceptance_test_text(raw_text: str) -> str:
    """Format acceptance test text for display by removing list prefix if present.

    Args:
        raw_text: The raw acceptance test text from specifications

    Returns:
        Formatted text with "- " prefix removed if present
    """
    if raw_text.startswith("- "):
        return raw_text[2:]
    return raw_text


class StateHandler(ABC):
    """Abstract base class for state handlers that process state machine transitions."""

    @abstractmethod
    def handle(
        self, _segments: list[str], _snapshot: RenderContextSnapshot, _previous_state_segments: list[str]
    ) -> None:
        """Handle a state transition.

        Args:
            segments: The state string split by '_' character
            snapshot: The current render context snapshot
            previous_state_segments: The previous state segments
        """
        pass


class FridReadyHandler(StateHandler):
    """Handler for READY_FOR_FRID_IMPLEMENTATION state."""

    def __init__(self, tui, unittests_script: Optional[str], conformance_tests_script: Optional[str]):
        """Initialize handler with TUI instance.

        Args:
            tui: The Plain2CodeTUI instance
        """
        self.tui = tui
        self.unittests_script = unittests_script
        self.conformance_tests_script = conformance_tests_script

    def handle(self, _: list[str], snapshot: RenderContextSnapshot, _previous_state_segments: list[str]) -> None:
        """Handle READY_FOR_FRID_IMPLEMENTATION state."""
        # Update FRID text
        rendering_functionality_text = f"{tui_components.FRIDProgress.RENDERING_FUNCTIONALITY_TEXT} {snapshot.frid_context.functional_requirement_text}"
        get_frid_progress(self.tui).update_fr_text(rendering_functionality_text)

        # Set progress states
        update_progress_item_status(self.tui, TUIComponents.FRID_PROGRESS_RENDER_FR.value, ProgressItem.PROCESSING)
        if self.conformance_tests_script is not None:
            update_progress_item_status(
                self.tui, TUIComponents.FRID_PROGRESS_CONFORMANCE_TEST.value, ProgressItem.PENDING
            )
        # Reset others to PENDING if this is a restart/loop
        if self.unittests_script is not None:
            update_progress_item_status(self.tui, TUIComponents.FRID_PROGRESS_UNIT_TEST.value, ProgressItem.PENDING)
        update_progress_item_status(self.tui, TUIComponents.FRID_PROGRESS_REFACTORING.value, ProgressItem.PENDING)

        # Set substate for initial implementation
        update_progress_item_substates(
            self.tui,
            TUIComponents.FRID_PROGRESS_RENDER_FR.value,
            [Substate("Initial implementation")],
        )


class UnitTestsHandler(StateHandler):
    """Handler for PROCESSING_UNIT_TESTS state."""

    def __init__(self, tui, unittests_script: Optional[str], conformance_tests_script: Optional[str]):
        """Initialize handler with TUI instance.

        Args:
            tui: The Plain2CodeTUI instance
        """
        self.tui = tui
        self.unittests_script = unittests_script
        self.conformance_tests_script = conformance_tests_script

    def handle(
        self, segments: list[str], _snapshot: RenderContextSnapshot, _previous_state_segments: list[str]
    ) -> None:
        """Handle PROCESSING_UNIT_TESTS state."""
        if segments[2] == States.UNIT_TESTS_READY.value:
            if self.unittests_script is not None:
                update_progress_item_status(
                    self.tui, TUIComponents.FRID_PROGRESS_UNIT_TEST.value, ProgressItem.PROCESSING
                )

            # Clear substates from completed implementation phase
            if self.unittests_script is not None:
                update_progress_item_substates(
                    self.tui,
                    TUIComponents.FRID_PROGRESS_UNIT_TEST.value,
                    [Substate("Running unit tests")],
                )

        if segments[2] == States.UNIT_TESTS_FAILED.value:
            if self.unittests_script is not None:
                update_progress_item_substates(
                    self.tui,
                    TUIComponents.FRID_PROGRESS_UNIT_TEST.value,
                    [Substate("Fixing unit tests")],
                )


class RefactoringHandler(StateHandler):
    """Handler for REFACTORING_CODE state."""

    def __init__(self, tui, unittests_script: Optional[str], conformance_tests_script: Optional[str]):
        """Initialize handler with TUI instance.

        Args:
            tui: The Plain2CodeTUI instance
        """
        self.tui = tui
        self.unittests_script = unittests_script
        self.conformance_tests_script = conformance_tests_script

    def handle(self, segments: list[str], _snapshot: RenderContextSnapshot, previous_state_segments: list[str]) -> None:
        """Handle REFACTORING_CODE state."""
        if len(previous_state_segments) == 2 and previous_state_segments[1] == States.STEP_COMPLETED.value:
            update_progress_item_status(
                self.tui, TUIComponents.FRID_PROGRESS_REFACTORING.value, ProgressItem.PROCESSING
            )

        if len(segments) == 3:
            if segments[2] == States.READY_FOR_REFACTORING.value:
                update_progress_item_substates(
                    self.tui,
                    TUIComponents.FRID_PROGRESS_REFACTORING.value,
                    [Substate("Refactoring code")],
                )
        if len(segments) > 3:
            if segments[3] == States.UNIT_TESTS_READY.value:
                update_progress_item_substates(
                    self.tui,
                    TUIComponents.FRID_PROGRESS_REFACTORING.value,
                    [Substate("Refactoring code", children=[Substate("Running unit tests")])],
                )
            elif segments[3] == States.UNIT_TESTS_FAILED.value:
                update_progress_item_substates(
                    self.tui,
                    TUIComponents.FRID_PROGRESS_REFACTORING.value,
                    [Substate("Refactoring code", children=[Substate("Fixing unit tests")])],
                )


class ConformanceTestsHandler(StateHandler):
    """Handler for PROCESSING_CONFORMANCE_TESTS state."""

    def __init__(self, tui, unittests_script: Optional[str], conformance_tests_script: Optional[str]):
        """Initialize handler with TUI instance.

        Args:
            tui: The Plain2CodeTUI instance
        """
        self.tui = tui
        self.unittests_script = unittests_script
        self.conformance_tests_script = conformance_tests_script

    def handle(self, segments: list[str], snapshot: RenderContextSnapshot, previous_state_segments: list[str]) -> None:
        """Handle PROCESSING_CONFORMANCE_TESTS state."""
        if previous_state_segments[1] == States.REFACTORING_CODE.value:
            if self.conformance_tests_script is not None:
                update_progress_item_status(
                    self.tui, TUIComponents.FRID_PROGRESS_CONFORMANCE_TEST.value, ProgressItem.PROCESSING
                )

        if segments[2] != States.POSTPROCESSING_CONFORMANCE_TESTS.value:
            if segments[2] == States.CONFORMANCE_TESTING_INITIALISED.value:
                if snapshot.conformance_tests_running_context.conformance_test_phase_index == 0:
                    rendering_text = f"Rendering conformance tests for functional requirement {snapshot.conformance_tests_running_context.current_testing_frid}"
                    update_progress_item_substates(
                        self.tui,
                        TUIComponents.FRID_PROGRESS_CONFORMANCE_TEST.value,
                        [Substate(rendering_text)],
                    )
                else:
                    acceptance_test = snapshot.conformance_tests_running_context.get_current_acceptance_test()
                    acceptance_test_text = f"Rendering acceptance test: {format_acceptance_test_text(acceptance_test)}"  # type: ignore
                    update_progress_item_substates(
                        self.tui,
                        TUIComponents.FRID_PROGRESS_CONFORMANCE_TEST.value,
                        [Substate(acceptance_test_text)],
                    )
            elif segments[2] == States.CONFORMANCE_TEST_GENERATED.value:
                update_progress_item_substates(
                    self.tui,
                    TUIComponents.FRID_PROGRESS_CONFORMANCE_TEST.value,
                    [Substate("Preparing testing environment for conformance tests")],
                )
            elif segments[2] == States.CONFORMANCE_TEST_ENV_PREPARED.value:
                running_text = f"Running conformance tests for functional requirement {snapshot.conformance_tests_running_context.current_testing_frid}"
                update_progress_item_substates(
                    self.tui, TUIComponents.FRID_PROGRESS_CONFORMANCE_TEST.value, [Substate(running_text)]
                )
            elif segments[2] == States.CONFORMANCE_TEST_FAILED.value:
                fixing_text = f"Fixing conformance tests for functional requirement {snapshot.conformance_tests_running_context.current_testing_frid}"
                update_progress_item_substates(
                    self.tui, TUIComponents.FRID_PROGRESS_CONFORMANCE_TEST.value, [Substate(fixing_text)]
                )
        else:
            if segments[3] == States.CONFORMANCE_TESTS_READY_FOR_SUMMARY.value:
                update_progress_item_substates(
                    self.tui,
                    TUIComponents.FRID_PROGRESS_CONFORMANCE_TEST.value,
                    [Substate("Summarizing conformance tests")],
                )


class ScriptOutputsHandler(StateHandler):
    """Handler for updating script output widgets."""

    def __init__(self, tui):
        """Initialize handler with TUI instance.

        Args:
            tui: The Plain2CodeTUI instance
        """
        self.tui = tui

    def handle(self, _segments: list[str], snapshot: RenderContextSnapshot, previous_state_segments: list[str]) -> None:
        # Update test scripts container
        container = self.tui.query_one(f"#{TUIComponents.TEST_SCRIPTS_CONTAINER.value}", TestScriptsContainer)

        if any(segment == States.UNIT_TESTS_READY.value for segment in previous_state_segments):
            if snapshot.script_execution_history.latest_unit_test_output_path:
                container.update_unit_test(
                    f"{ScriptOutputType.UNIT_TEST_OUTPUT_TEXT.value}{snapshot.script_execution_history.latest_unit_test_output_path}"
                )

        if len(previous_state_segments) > 2 and previous_state_segments[2] == States.CONFORMANCE_TEST_GENERATED.value:
            if snapshot.script_execution_history.latest_testing_environment_output_path:
                container.update_testing_env(
                    f"{ScriptOutputType.TESTING_ENVIRONMENT_OUTPUT_TEXT.value}{snapshot.script_execution_history.latest_testing_environment_output_path}"
                )

        if (
            len(previous_state_segments) > 2
            and previous_state_segments[2] == States.CONFORMANCE_TEST_ENV_PREPARED.value
        ):
            if snapshot.script_execution_history.latest_conformance_test_output_path:
                container.update_conformance_test(
                    f"{ScriptOutputType.CONFORMANCE_TEST_OUTPUT_TEXT.value}{snapshot.script_execution_history.latest_conformance_test_output_path}"
                )


class FridFullyImplementedHandler(StateHandler):
    """Handler for FRID_FULLY_IMPLEMENTED state."""

    def __init__(self, tui, unittests_script: Optional[str], conformance_tests_script: Optional[str]):
        """Initialize handler with TUI instance.

        Args:
            tui: The Plain2CodeTUI instance
        """
        self.tui = tui
        self.unittests_script = unittests_script
        self.conformance_tests_script = conformance_tests_script

    def handle(self, _: list[str], _snapshot: RenderContextSnapshot, _previous_state_segments: list[str]) -> None:
        """Handle FRID_FULLY_IMPLEMENTED state."""
        pass


class RenderSuccessHandler:
    """Handler for successful render completion."""

    def __init__(self, tui):
        """Initialize handler with TUI instance.

        Args:
            tui: The Plain2CodeTUI instance
        """
        self.tui = tui

    def handle(self, module_name: str, build_folder: str) -> None:
        """Handle successful render completion.

        Args:
            module_name: Name of the last module that completed rendering
            build_folder: The build folder path
        """
        display_success_message(self.tui, module_name, build_folder)


class RenderErrorHandler:
    """Handler for ERROR state."""

    def __init__(self, tui):
        """Initialize handler with TUI instance.

        Args:
            tui: The Plain2CodeTUI instance
        """
        self.tui = tui

    def handle(self, error_message: str) -> None:
        set_frid_progress_to_stopped(self.tui)
        display_error_message(self.tui, error_message)


class StateCompletionHandler(StateHandler):
    """Handler for state completion."""

    def __init__(self, tui, unittests_script: Optional[str], conformance_tests_script: Optional[str]):
        """Initialize handler with TUI instance.

        Args:
            tui: The Plain2CodeTUI instance
        """
        self.tui = tui
        self.unittests_script = unittests_script
        self.conformance_tests_script = conformance_tests_script

    def handle(self, segments: list[str], _snapshot: RenderContextSnapshot, previous_state_segments: list[str]) -> None:
        if len(previous_state_segments) < 2 or len(segments) < 2:
            return
        current_segment = segments[1]
        previous_segment = previous_state_segments[1]
        should_update_state = current_segment != previous_segment
        if not should_update_state:
            return

        if previous_segment == States.READY_FOR_FRID_IMPLEMENTATION.value:
            update_progress_item_status(self.tui, TUIComponents.FRID_PROGRESS_RENDER_FR.value, ProgressItem.COMPLETED)
        if previous_segment == States.PROCESSING_UNIT_TESTS.value:
            update_progress_item_status(self.tui, TUIComponents.FRID_PROGRESS_UNIT_TEST.value, ProgressItem.COMPLETED)
        if previous_segment == States.REFACTORING_CODE.value:
            update_progress_item_status(self.tui, TUIComponents.FRID_PROGRESS_REFACTORING.value, ProgressItem.COMPLETED)
        if previous_segment == States.PROCESSING_CONFORMANCE_TESTS.value:
            update_progress_item_status(
                self.tui, TUIComponents.FRID_PROGRESS_CONFORMANCE_TEST.value, ProgressItem.COMPLETED
            )
