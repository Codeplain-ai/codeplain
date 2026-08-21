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
    transition_frid_progress,
    update_progress_item_status,
    update_progress_item_substates,
)


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

        rendering_functionality_text = f"{tui_components.FRIDProgress.RENDERING_FUNCTIONALITY_TEXT}{snapshot.frid_context.frid}: {snapshot.frid_context.functional_requirement_text}"
        get_frid_progress(self.tui).update_functionality_text(rendering_functionality_text)

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

    def handle(self, rendered_code_path: str) -> None:
        """Handle successful render completion.

        Args:
            rendered_code_path: The path to the rendered code
        """
        display_success_message(self.tui, rendered_code_path)


class RenderErrorHandler:
    """Handler for ERROR state."""

    def __init__(self, tui):
        """Initialize handler with TUI instance.

        Args:
            tui: The Plain2CodeTUI instance
        """
        self.tui = tui

    def handle(self, error_message: str) -> None:
        transition_frid_progress(self.tui, None, ProgressItem.STOPPED)
        display_error_message(self.tui, error_message)


class ModuleConformanceTestsHandler(StateHandler):
    """Handler for the PROCESSING_MODULE_CONFORMANCE_TESTS phase.

    The phase is not nested inside IMPLEMENTING_FRID, so it reports into the conformance-tests
    progress item on its own: by the time it runs, every functionality has been implemented and the
    remaining work belongs to the module as a whole.
    """

    MODULE_CONFORMANCE_TESTS_TEXT = "Conformance tests of the whole module"

    def __init__(self, tui, unittests_script: Optional[str], conformance_tests_script: Optional[str]):
        """Initialize handler with TUI instance.

        Args:
            tui: The Plain2CodeTUI instance
        """
        self.tui = tui
        self.unittests_script = unittests_script
        self.conformance_tests_script = conformance_tests_script

    def handle(self, segments: list[str], snapshot: RenderContextSnapshot, previous_state_segments: list[str]) -> None:
        if len(segments) < 2:
            return

        if not previous_state_segments or previous_state_segments[0] != segments[0]:
            # Entering the phase: the per-functionality items are done, the module's tests are next.
            get_frid_progress(self.tui).update_functionality_text(self.MODULE_CONFORMANCE_TESTS_TEXT)
            update_progress_item_status(
                self.tui, TUIComponents.FRID_PROGRESS_CONFORMANCE_TEST.value, ProgressItem.PROCESSING
            )

        substate_text = self._get_substate_text(segments, snapshot)
        if substate_text is not None:
            update_progress_item_substates(
                self.tui, TUIComponents.FRID_PROGRESS_CONFORMANCE_TEST.value, [Substate(substate_text)]
            )

        if segments[1] == States.MODULE_FULLY_IMPLEMENTED.value:
            update_progress_item_status(
                self.tui, TUIComponents.FRID_PROGRESS_CONFORMANCE_TEST.value, ProgressItem.COMPLETED
            )

    def _get_substate_text(self, segments: list[str], snapshot: RenderContextSnapshot) -> Optional[str]:
        ctx = snapshot.module_conformance_tests_running_context
        phase = segments[1]

        if phase == States.MODULE_CONFORMANCE_TESTING_INITIALISED.value:
            return "Planning conformance tests covering all functionalities of the module"

        if phase == States.MODULE_CONFORMANCE_TESTS_PLANNED.value:
            if ctx is None or not ctx.number_of_batches:
                return "Implementing conformance tests for the module"
            return (
                f"Implementing conformance tests for the module "
                f"(batch {min(ctx.batches_rendered + 1, ctx.number_of_batches)} of {ctx.number_of_batches})"
            )

        if phase == States.MODULE_CONFORMANCE_TESTS_GENERATED.value:
            return "Preparing testing environment for conformance tests"

        if phase == States.MODULE_CONFORMANCE_TESTS_ENV_PREPARED.value:
            return f"Running conformance tests{self._describe_suite(ctx)}"

        if phase == States.MODULE_CONFORMANCE_TESTS_FAILED.value:
            return f"Fixing conformance tests{self._describe_suite(ctx)}"

        if phase == States.PROCESSING_UNIT_TESTS.value:
            return "Adjusting unit tests after the implementation code was fixed"

        if phase == States.POSTPROCESSING_MODULE_CONFORMANCE_TESTS.value:
            if len(segments) > 2 and segments[2] == States.MODULE_CONFORMANCE_TESTS_READY_FOR_SUMMARY.value:
                return "Summarizing conformance tests"
            return None

        return None

    def _describe_suite(self, ctx) -> str:
        if ctx is None:
            return ""

        suite = ctx.current_suite
        if suite.is_own_module:
            return f" of module {suite.module_name}"

        if suite.frid is not None:
            return f" of functionality {suite.frid} of module {suite.module_name}"

        return f" of required module {suite.module_name}"


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
