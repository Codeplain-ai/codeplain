import threading
from copy import deepcopy
from typing import Callable, Optional

import file_utils
import git_utils
import plain_spec
from codeplain_REST_api import CodeplainAPI
from event_bus import EventBus
from plain2code_console import console
from plain2code_events import RenderContextSnapshot
from plain2code_state import RunState
from plain_modules import PlainModule
from render_machine import triggers
from render_machine.conformance_tests import CONFORMANCE_TESTS_DEFINITION_FILE_NAME, ConformanceTests
from render_machine.render_types import (
    AcceptanceTestPhase,
    ConformanceTestsRunningContext,
    FridContext,
    ScriptExecutionHistory,
    TestExecutionPhase,
    UnitTestsRunningContext,
)

MAX_UNITTEST_FIX_ATTEMPTS = 20
MAX_FUNCTIONAL_REQUIREMENT_RENDER_ATTEMPTS_FAILED_UNIT_DURING_CONFORMANCE_TESTS = 2


class RenderContext:
    def __init__(
        self,
        codeplain_api,
        memory_manager,
        plain_module: PlainModule,
        build_folder: str,
        build_dest: str,
        conformance_tests_folder: str,
        conformance_tests_dest: str,
        unittests_script: str,
        conformance_tests_script: str,
        prepare_environment_script: str,
        copy_build: bool,
        copy_conformance_tests: bool,
        render_range: list[str] | None,
        render_conformance_tests: bool,
        base_folder: str,
        run_state: RunState,
        event_bus: EventBus,
        test_script_timeout: Optional[int] = None,
        stop_event: Optional[threading.Event] = None,
        enter_pause_event: Optional[threading.Event] = None,
        use_agent: bool = False,
    ):
        self.codeplain_api: CodeplainAPI = codeplain_api
        self.memory_manager = memory_manager
        self.plain_module = plain_module
        self.plain_source_tree = plain_module.plain_source
        self.module_name = plain_module.module_name
        self.template_dirs = plain_module.template_dirs
        self.required_modules = plain_module.required_modules
        self.build_folder = build_folder
        self.build_dest = build_dest
        self.conformance_tests_folder = conformance_tests_folder
        self.conformance_tests_dest = conformance_tests_dest
        self.unittests_script = unittests_script
        self.conformance_tests_script = conformance_tests_script
        self.prepare_environment_script = prepare_environment_script
        self.copy_build = copy_build
        self.copy_conformance_tests = copy_conformance_tests
        self.render_range = render_range
        self.render_conformance_tests = render_conformance_tests
        self.base_folder = base_folder
        self.run_state = run_state
        self.event_bus = event_bus
        self.stop_event = stop_event
        self.enter_pause_event = enter_pause_event
        self.use_agent = use_agent
        self.script_execution_history = ScriptExecutionHistory()
        self.starting_frid = None
        self.test_script_timeout = test_script_timeout

        resources_list = []
        plain_spec.collect_linked_resources(plain_module.plain_source, resources_list, None, True)
        self.all_linked_resources = file_utils.load_linked_resources(
            plain_module.template_dirs, resources_list, plain_module.module_name
        )

        # Initialize context objects
        self.frid_context: Optional[FridContext] = None
        self.unit_tests_running_context: Optional[UnitTestsRunningContext] = None
        self.conformance_tests_running_context: Optional[ConformanceTestsRunningContext] = None
        # Constants that should remain for a single frid, but possible over multiple rerenderings of the same frid
        self.functional_requirements_render_attempts_failed_unit_during_conformance_tests = 0
        # Initialize conformance tests utilities
        self.conformance_tests = ConformanceTests(
            conformance_tests_folder=self.conformance_tests_folder,
            conformance_tests_definition_file_name=CONFORMANCE_TESTS_DEFINITION_FILE_NAME,
        )

        self.machine = None
        self.last_error_message: str | None = None

    def set_machine(self, machine):
        self.machine = machine

    def dispatch_error(self, error_message: str):
        """Log error, store it, and dispatch HANDLE_ERROR trigger.

        Args:
            error_message: The error message to log and display to the user.
        """
        console.error(error_message)
        self.last_error_message = error_message
        self.machine.dispatch(triggers.HANDLE_ERROR)

    def create_snapshot(self) -> RenderContextSnapshot:
        return RenderContextSnapshot(
            frid_context=deepcopy(self.frid_context) if self.frid_context else None,
            conformance_tests_running_context=(
                deepcopy(self.conformance_tests_running_context) if self.conformance_tests_running_context else None
            ),
            unit_tests_running_context=(
                deepcopy(self.unit_tests_running_context) if self.unit_tests_running_context else None
            ),
            script_execution_history=deepcopy(self.script_execution_history),
            module_name=self.module_name,
        )

    def get_required_modules_functionalities(self):
        required_modules_functionalities = {}
        if self.required_modules is not None and len(self.required_modules) > 0:
            for required_module in self.required_modules:
                required_modules_functionalities.update(required_module.get_functionalities())

        return required_modules_functionalities

    def start_implementing_frid(self):
        if self.starting_frid is not None:
            frid = self.starting_frid
            self.starting_frid = None
        elif self.frid_context is None:
            frid = plain_spec.get_first_frid(self.plain_source_tree)
        else:
            frid = plain_spec.get_next_frid(self.plain_source_tree, self.frid_context.frid)

        specifications, _ = plain_spec.get_specifications_for_frid(self.plain_source_tree, frid)
        functional_requirement_text = specifications[plain_spec.FUNCTIONAL_REQUIREMENTS][-1]

        resources_list = []
        plain_spec.collect_linked_resources(self.plain_source_tree, resources_list, None, True, frid)

        linked_resources = {}
        for resource in resources_list:
            linked_resources[resource["target"]] = self.all_linked_resources[resource["target"]]

        self.frid_context = FridContext(
            frid=frid,
            specifications=specifications,
            functional_requirement_text=functional_requirement_text,
            linked_resources=linked_resources,
            functional_requirement_render_attempts=0,
        )
        self.run_state.current_frid = frid
        return

    def has_next_frid(self) -> bool:
        next_frid = plain_spec.get_next_frid(self.plain_source_tree, self.frid_context.frid)
        if self.render_range is None or len(self.render_range) == 0:
            return next_frid is not None

        return next_frid is not None and int(next_frid) <= int(self.render_range[-1])

    def finish_implementing_frid(self):
        self.functional_requirements_render_attempts_failed_unit_during_conformance_tests = 0
        self.run_state.increment_rendered_functionalities()

    def should_run_unit_tests(self) -> bool:
        return self.unittests_script is not None

    def should_run_conformance_tests(self) -> bool:
        return self.conformance_tests_script is not None

    def start_unittests_processing(self):
        self.unit_tests_running_context = UnitTestsRunningContext(fix_attempts=0)
        self.run_state.increment_unittest_batch_id()

    def _get_first_frid_conformance_test_running_context(self, module: PlainModule | None):
        conformance_tests_running_context = self.conformance_tests_running_context

        if module is None:
            conformance_tests_running_context.current_testing_module_name = self.module_name
            if not conformance_tests_running_context.conformance_tests_json_has_module_populated(
                conformance_tests_running_context.current_testing_module_name
            ):
                conformance_tests_running_context.set_conformance_tests_json(
                    conformance_tests_running_context.current_testing_module_name,
                    {},
                )
        else:
            conformance_tests_running_context.current_testing_module_name = module.module_name
            conformance_tests_running_context.set_conformance_tests_json(
                conformance_tests_running_context.current_testing_module_name,
                self.conformance_tests.get_conformance_tests_json(
                    conformance_tests_running_context.current_testing_module_name
                ),
            )

        if module is None:
            conformance_tests_running_context.current_testing_frid = plain_spec.get_first_frid(self.plain_source_tree)
        else:
            conformance_tests_running_context.current_testing_frid = next(
                iter(
                    conformance_tests_running_context.get_conformance_tests_json(
                        conformance_tests_running_context.current_testing_module_name
                    )
                )
            )

        return conformance_tests_running_context

    def get_first_conformance_tests_running_context(self):
        if self.required_modules is None or len(self.required_modules) == 0:
            return self._get_first_frid_conformance_test_running_context(None)
        else:
            return self._get_first_frid_conformance_test_running_context(self.required_modules[0])

    def get_next_conformance_tests_running_context(self):
        conformance_tests_running_context = self.conformance_tests_running_context
        if conformance_tests_running_context.current_testing_module_name == self.module_name:
            conformance_tests_running_context.current_testing_frid = plain_spec.get_next_frid(
                self.plain_source_tree,
                self.conformance_tests_running_context.current_testing_frid,
            )
        else:
            all_frids = list(
                conformance_tests_running_context.get_conformance_tests_json(
                    conformance_tests_running_context.current_testing_module_name
                ).keys()
            )
            current_index = all_frids.index(conformance_tests_running_context.current_testing_frid)
            if current_index + 1 < len(all_frids):
                conformance_tests_running_context.current_testing_frid = all_frids[current_index + 1]
            else:
                next_module_index = -1
                for i, required_module in enumerate(self.required_modules):
                    if required_module.module_name == conformance_tests_running_context.current_testing_module_name:
                        next_module_index = i + 1
                        break

                if next_module_index < len(self.required_modules):
                    conformance_tests_running_context = self._get_first_frid_conformance_test_running_context(
                        self.required_modules[next_module_index]
                    )
                else:
                    conformance_tests_running_context = self._get_first_frid_conformance_test_running_context(None)

        return conformance_tests_running_context

    def finish_unittests_processing(self):
        existing_files = file_utils.list_all_text_files(self.build_folder)

        # TODO: Double check if this logic is what we want
        for file_name in self.unit_tests_running_context.changed_files:
            if file_name not in existing_files:
                self.frid_context.changed_files.discard(file_name)
            else:
                self.frid_context.changed_files.add(file_name)
        self.unit_tests_running_context.fix_attempts = 1

    def start_fixing_unit_tests(self, on_limit_exceeded: Callable):
        self.unit_tests_running_context.fix_attempts += 1
        if self.unit_tests_running_context.fix_attempts > MAX_UNITTEST_FIX_ATTEMPTS:
            on_limit_exceeded()

    def _on_unit_test_limit_exceeded_in_implementation(self):
        self.machine.dispatch(triggers.RESTART_FRID_PROCESSING)

    def _on_unit_test_limit_exceeded_in_conformance_tests(self):
        self.functional_requirements_render_attempts_failed_unit_during_conformance_tests += 1
        if (
            self.functional_requirements_render_attempts_failed_unit_during_conformance_tests
            >= MAX_FUNCTIONAL_REQUIREMENT_RENDER_ATTEMPTS_FAILED_UNIT_DURING_CONFORMANCE_TESTS
        ):
            error_msg = f"Failed to adjust the unit tests after implementation code was update while fixing the conformance tests for functionality {self.frid_context.frid} for the {MAX_FUNCTIONAL_REQUIREMENT_RENDER_ATTEMPTS_FAILED_UNIT_DURING_CONFORMANCE_TESTS} times."
            self.dispatch_error(error_msg)
        else:
            console.info(
                f"Failed to adjust the unit tests after implementation code was updated while fixing the conformance tests for functionality {self.frid_context.frid}."
            )
            console.info(f"Restarting rendering the functionality {self.frid_context.frid} from scratch.")
            self.machine.dispatch(triggers.RESTART_FRID_PROCESSING)

    def _on_unit_test_limit_exceeded_in_refactoring(self):
        git_utils.revert_changes(self.build_folder)
        self.machine.dispatch(triggers.START_NEW_REFACTORING_ITERATION)

    def start_conformance_tests_processing(self):
        console.info("Implementing conformance tests...")
        current_frid_specifications, _ = plain_spec.get_specifications_for_frid(
            self.plain_source_tree, self.frid_context.frid
        )
        self.conformance_tests_running_context = ConformanceTestsRunningContext(
            current_testing_module_name=self.module_name,
            current_testing_frid=self.frid_context.frid,
            current_testing_frid_specifications=current_frid_specifications,
            fix_attempts=0,
            conformance_tests_json=self.conformance_tests.get_conformance_tests_json(self.module_name),
            conformance_tests_render_attempts=0,
            should_prepare_testing_environment=True,
            frid_being_implemented=self.frid_context.frid,
        )

    def finish_conformance_tests_processing(self):
        self.conformance_tests_running_context = None

    # ========== Helper Methods for Conformance Test Execution ==========

    def _should_run_current_frid_tests(self) -> bool:
        """Check if we should run/continue testing the current FRID."""
        ctx = self.conformance_tests_running_context
        return (
            ctx.execution_phase == TestExecutionPhase.TESTING_CURRENT_FRID
            and ctx.current_testing_module_name == self.module_name
            and ctx.current_testing_frid == ctx.frid_being_implemented
        )

    def _has_more_acceptance_test_phases(self) -> bool:
        """Check if there are more acceptance test phases to run."""
        ctx = self.conformance_tests_running_context

        if ctx.acceptance_test_phase == AcceptanceTestPhase.NOT_APPLICABLE:
            return False
        if ctx.acceptance_test_phase == AcceptanceTestPhase.COMPLETED:
            return False

        acceptance_tests = self.frid_context.specifications.get(plain_spec.ACCEPTANCE_TESTS, [])
        return ctx.acceptance_tests_completed < len(acceptance_tests)

    def _start_regression_phase(self):
        """Transition to regression testing phase."""
        ctx = self.conformance_tests_running_context

        # Only reset code_changed flag if starting fresh regression (not restarting after fix)
        if ctx.execution_phase != TestExecutionPhase.RETRYING_AFTER_CODE_CHANGE:
            ctx.code_changed_during_regression = False

        ctx.execution_phase = TestExecutionPhase.RUNNING_REGRESSION
        ctx.current_testing_frid = None  # Will be set by get_first_conformance_tests_running_context

    def _get_next_test_to_run(self):
        """Determine which test to run next based on current phase."""
        ctx = self.conformance_tests_running_context

        if ctx.current_testing_frid is None:
            return self.get_first_conformance_tests_running_context()
        else:
            return self.get_next_conformance_tests_running_context()

    def _has_reached_implementation_frid(self) -> bool:
        """Check if regression has reached the FRID being implemented."""
        ctx = self.conformance_tests_running_context
        return (
            ctx.execution_phase == TestExecutionPhase.RUNNING_REGRESSION
            and ctx.current_testing_module_name == self.module_name
            and (ctx.current_testing_frid is None or ctx.current_testing_frid == ctx.frid_being_implemented)
        )

    def _setup_test_specifications(self):
        """Load specifications for the current test."""
        ctx = self.conformance_tests_running_context

        if ctx.current_testing_module_name == self.module_name:
            ctx.current_testing_frid_specifications, _ = plain_spec.get_specifications_for_frid(
                self.plain_source_tree, ctx.current_testing_frid
            )
        else:
            ctx.current_testing_frid_specifications = ctx.get_conformance_tests_json(ctx.current_testing_module_name)[
                ctx.current_testing_frid
            ]["functional_requirement"]

    # ========== Phase Handlers ==========

    def _handle_test_regeneration(self):
        """Handle regeneration of conformance tests after too many failures."""
        ctx = self.conformance_tests_running_context

        console.info(f"Recreating conformance tests for functionality {ctx.current_testing_frid}.")

        existing_folder = ctx.get_conformance_tests_json(ctx.current_testing_module_name).pop(ctx.current_testing_frid)
        file_utils.delete_folder(existing_folder["folder_name"])

        ctx.conformance_tests_render_attempts += 1
        ctx.fix_attempts = 0
        ctx.regenerating_conformance_tests = False

    def _handle_retry_after_code_change(self):
        """Re-run the test that failed and triggered a code change."""
        ctx = self.conformance_tests_running_context

        # The test that failed is still in current_testing_frid - just re-run it
        self._setup_test_specifications()

        if ctx.current_conformance_tests_exist():
            self.machine.dispatch(triggers.MARK_CONFORMANCE_TESTS_READY)

    def _on_conformance_test_passed_after_retry(self):
        """Called when a test passes after being retried due to code changes."""
        ctx = self.conformance_tests_running_context

        if ctx.execution_phase == TestExecutionPhase.RETRYING_AFTER_CODE_CHANGE:
            # Test passed after code change - mark that code changed and restart regression
            ctx.code_changed_during_regression = True
            ctx.test_that_triggered_code_change = None
            self._start_regression_phase()

    def _handle_current_frid_testing(self):
        """Handle incremental testing of the current FRID being implemented."""
        ctx = self.conformance_tests_running_context

        # Wait for tests to be rendered
        if not ctx.current_conformance_tests_exist():
            return

        # Initialize acceptance test phase AFTER tests exist and pass
        # This ensures full conformance tests run first before acceptance tests
        if ctx.acceptance_test_phase == AcceptanceTestPhase.NOT_STARTED:
            acceptance_tests = self.frid_context.specifications.get(plain_spec.ACCEPTANCE_TESTS)
            if not acceptance_tests:
                ctx.acceptance_test_phase = AcceptanceTestPhase.NOT_APPLICABLE
                # Increment counter immediately to signal "we're starting the test process"
                # This prevents re-running the test after it's rendered
                ctx.acceptance_tests_completed = 1
            else:
                ctx.acceptance_test_phase = AcceptanceTestPhase.IN_PROGRESS

        # Handle case: No acceptance tests
        if ctx.acceptance_test_phase == AcceptanceTestPhase.NOT_APPLICABLE:
            if ctx.acceptance_tests_completed == 1:
                # Test has run once - move to regression
                ctx.acceptance_test_phase = AcceptanceTestPhase.COMPLETED
                self._start_regression_phase()
                self._handle_regression_testing()
                return
            else:
                # This shouldn't happen since we set completed=1 during initialization
                raise RuntimeError(f"Unexpected state: acceptance_tests_completed={ctx.acceptance_tests_completed}")

        # Handle case: Has acceptance tests
        if ctx.acceptance_test_phase == AcceptanceTestPhase.IN_PROGRESS:
            if self._has_more_acceptance_test_phases():
                # Run next phase
                if ctx.acceptance_tests_completed == 0:
                    ctx.current_testing_frid_high_level_implementation_plan = None

                ctx.acceptance_tests_completed += 1
                acceptance_tests = self.frid_context.specifications[plain_spec.ACCEPTANCE_TESTS][
                    : ctx.acceptance_tests_completed
                ]
                ctx.get_conformance_tests_json(ctx.current_testing_module_name)[ctx.current_testing_frid][
                    plain_spec.ACCEPTANCE_TESTS
                ] = acceptance_tests
                return
            else:
                # All phases done - move to regression
                ctx.acceptance_test_phase = AcceptanceTestPhase.COMPLETED
                self._start_regression_phase()
                self._handle_regression_testing()
                return

        # Should not reach here
        raise RuntimeError(f"Unexpected acceptance test phase: {ctx.acceptance_test_phase}")

    def _handle_regression_testing(self):
        """Handle regression testing of all earlier FRIDs."""

        # Get next test to run
        self.conformance_tests_running_context = self._get_next_test_to_run()

        # Get reference to the updated context
        ctx = self.conformance_tests_running_context

        # Set up specs and run test
        self._setup_test_specifications()

        if ctx.current_conformance_tests_exist():
            # Check if this is the implementation FRID (last test to run)
            if self._has_reached_implementation_frid():
                # Reached implementation FRID - only re-run it if code changed during regression
                if ctx.code_changed_during_regression:
                    # Code changed - run the implementation FRID again to verify no regression
                    # After it passes, mark as completed on next iteration
                    ctx.execution_phase = TestExecutionPhase.COMPLETED
                else:
                    # No code changes - skip re-running implementation FRID, mark as completed immediately
                    ctx.execution_phase = TestExecutionPhase.COMPLETED
                    self.machine.dispatch(triggers.MARK_ALL_CONFORMANCE_TESTS_PASSED)
                    return

            self.machine.dispatch(triggers.MARK_CONFORMANCE_TESTS_READY)

    # ========== Main Conformance Test Orchestration ==========

    def start_conformance_tests_for_frid(self):
        """
        Orchestrate conformance test execution.

        Flow:
        1. Handle test regeneration (if needed)
        2. Handle test passed after retry (transition to regression)
        3. Handle code changes (retry failed test)
        4. Handle current FRID testing (incremental phases)
        5. Handle regression testing (all earlier FRIDs)
        6. Detect completion
        """

        ctx = self.conformance_tests_running_context

        # ========== STEP 1: Handle Test Regeneration ==========
        if ctx.regenerating_conformance_tests:
            self._handle_test_regeneration()
            return

        # ========== STEP 2: Test Passed After Retry - Transition to Regression ==========
        # This happens when MOVE_TO_NEXT_CONFORMANCE_TEST is triggered after a retry succeeds
        if (
            ctx.execution_phase == TestExecutionPhase.RETRYING_AFTER_CODE_CHANGE
            and ctx.test_that_triggered_code_change is not None
        ):
            # The test that had code changes has now passed
            self._on_conformance_test_passed_after_retry()
            # Fall through to handle regression

        # ========== STEP 3: Handle Code Changes (Retry) ==========
        if ctx.execution_phase == TestExecutionPhase.RETRYING_AFTER_CODE_CHANGE:
            self._handle_retry_after_code_change()
            return

        # ========== STEP 3: Handle Current FRID Testing ==========
        if self._should_run_current_frid_tests():
            self._handle_current_frid_testing()
            return

        # ========== STEP 4: Handle Regression Testing ==========
        if ctx.execution_phase == TestExecutionPhase.RUNNING_REGRESSION:
            self._handle_regression_testing()
            return

        # ========== STEP 5: Completion ==========
        if ctx.execution_phase == TestExecutionPhase.COMPLETED:
            self.machine.dispatch(triggers.MARK_ALL_CONFORMANCE_TESTS_PASSED)
            return

        # Should never reach here
        raise RuntimeError(f"Unexpected execution phase: {ctx.execution_phase}")

    def start_render_completed(self):
        self.run_state.set_render_succeeded(True)

    def start_render_failed(self):
        self.run_state.set_render_succeeded(False)
