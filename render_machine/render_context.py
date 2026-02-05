from copy import deepcopy
from typing import Optional

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
    ConformanceTestsRunningContext,
    FridContext,
    ScriptExecutionHistory,
    UnitTestsRunningContext,
)

MAX_UNITTEST_FIX_ATTEMPTS = 20
MAX_CODE_GENERATION_RETRIES = 2
MAX_CONFORMANCE_TEST_RERENDER_ATTEMPTS = 1
MAX_REFACTORING_ITERATIONS = 5
MAX_CONFORMANCE_TEST_FIX_ATTEMPTS = 20
MAX_FUNCTIONAL_REQUIREMENT_RENDER_ATTEMPTS_FAILED_UNIT_DURING_CONFORMANCE_TESTS = 2


class RenderContext:
    def __init__(
        self,
        codeplain_api,
        memory_manager,
        module_name: str,
        plain_source_tree: dict,
        required_modules: list[PlainModule],
        template_dirs: list[str],
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
        verbose: bool,
        run_state: RunState,
        event_bus: EventBus,
    ):
        self.codeplain_api: CodeplainAPI = codeplain_api
        self.memory_manager = memory_manager
        self.plain_source_tree = plain_source_tree
        self.module_name = module_name
        self.template_dirs = template_dirs
        self.required_modules = required_modules
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
        self.verbose = verbose
        self.run_state = run_state
        self.event_bus = event_bus
        self.script_execution_history = ScriptExecutionHistory()
        self.starting_frid = None

        self.previous_state = None

        resources_list = []
        plain_spec.collect_linked_resources(plain_source_tree, resources_list, None, True)
        self.all_linked_resources = file_utils.load_linked_resources(template_dirs, resources_list)

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
            verbose=verbose,
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

        if frid is None:
            # If frid context is empty, it means that all frids have been implemented
            self.frid_context = None
            self.machine.dispatch(triggers.PREPARE_FINAL_OUTPUT)
            return

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
        return

    def check_frid_iteration_limit(self):
        # If frid context is not set, it means that all frids have been implemented
        if self.frid_context is None:
            return

        if self.frid_context.functional_requirement_render_attempts >= MAX_CODE_GENERATION_RETRIES:
            error_msg = f"Unittests could not be fixed after rendering the functional requirement {self.frid_context.frid} for the {MAX_CODE_GENERATION_RETRIES} times."
            self.dispatch_error(error_msg)

        self.frid_context.functional_requirement_render_attempts += 1

        if self.frid_context.functional_requirement_render_attempts > 1:
            # this if is intended just for logging
            console.info(
                f"Unittests could not be fixed after rendering the functional requirement. "
                f"Restarting rendering the functional requirement {self.frid_context.frid} from scratch."
            )

    def finish_implementing_frid(self):
        self.functional_requirements_render_attempts_failed_unit_during_conformance_tests = 0

    def should_run_unit_tests(self) -> bool:
        return self.unittests_script is not None

    def should_run_conformance_tests(self) -> bool:
        return self.conformance_tests_script is not None

    def finish_frid_implementation_step(self):
        pass

    def start_unittests_processing(self):
        self.unit_tests_running_context = UnitTestsRunningContext(fix_attempts=0)
        self.run_state.increment_unittest_batch_id()

    def start_unittests_processing_in_implementation(self):
        self.start_unittests_processing()

    def start_unittests_processing_in_refactoring(self):
        self.start_unittests_processing()

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
            conformance_tests_running_context.current_testing_module_name = module.name
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
                self.plain_source_tree, self.conformance_tests_running_context.current_testing_frid
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
                    if required_module.name == conformance_tests_running_context.current_testing_module_name:
                        next_module_index = i + 1
                        break

                if next_module_index < len(self.required_modules):
                    conformance_tests_running_context = self._get_first_frid_conformance_test_running_context(
                        self.required_modules[next_module_index]
                    )
                else:
                    conformance_tests_running_context = self._get_first_frid_conformance_test_running_context(None)

        return conformance_tests_running_context

    def start_unittests_processing_in_conformance_tests(self):
        self.start_unittests_processing()
        self.conformance_tests_running_context = self.get_first_conformance_tests_running_context()

    def finish_unittests_processing(self):
        existing_files = file_utils.list_all_text_files(self.build_folder)

        # TODO: Double check if this logic is what we want
        for file_name in self.unit_tests_running_context.changed_files:
            if file_name not in existing_files:
                self.frid_context.changed_files.discard(file_name)
            else:
                self.frid_context.changed_files.add(file_name)
        self.unit_tests_running_context.fix_attempts = 1

    def finish_unittests_processing_during_implementation(self):
        self.finish_unittests_processing()

    def start_fixing_unit_tests(self):
        self.unit_tests_running_context.fix_attempts += 1

        if self.unit_tests_running_context.fix_attempts > MAX_UNITTEST_FIX_ATTEMPTS:
            self.machine.dispatch(triggers.RESTART_FRID_PROCESSING)

    def start_fixing_unit_tests_in_conformance_tests(self):
        self.unit_tests_running_context.fix_attempts += 1

        if self.unit_tests_running_context.fix_attempts > MAX_UNITTEST_FIX_ATTEMPTS:
            self.functional_requirements_render_attempts_failed_unit_during_conformance_tests += 1
            if (
                self.functional_requirements_render_attempts_failed_unit_during_conformance_tests
                >= MAX_FUNCTIONAL_REQUIREMENT_RENDER_ATTEMPTS_FAILED_UNIT_DURING_CONFORMANCE_TESTS
            ):
                error_msg = f"Failed to adjust unit tests after implementation code was update while fixing conformance tests for functional requirement {self.frid_context.frid} for the {MAX_FUNCTIONAL_REQUIREMENT_RENDER_ATTEMPTS_FAILED_UNIT_DURING_CONFORMANCE_TESTS} times."
                self.dispatch_error(error_msg)
            else:
                console.info(
                    f"Failed to adjust unit tests after implementation code was update while fixing conformance tests for functional requirement {self.frid_context.frid}."
                )
                console.info(f"Restarting rendering the functional requirement {self.frid_context.frid} from scratch.")
                self.machine.dispatch(triggers.RESTART_FRID_PROCESSING)

    def start_fixing_unit_tests_in_refactoring(self):
        self.unit_tests_running_context.fix_attempts += 1

        if self.unit_tests_running_context.fix_attempts > MAX_UNITTEST_FIX_ATTEMPTS:
            git_utils.revert_changes(self.build_folder)
            self.machine.dispatch(triggers.START_NEW_REFACTORING_ITERATION)

    def start_refactoring_code(self):

        if self.frid_context.refactoring_iteration == 0:
            console.info("Refactoring the generated code...")

        self.frid_context.refactoring_iteration += 1

        if self.frid_context.refactoring_iteration >= MAX_REFACTORING_ITERATIONS:
            if self.verbose:
                console.info(
                    f"Refactoring iterations limit of {MAX_REFACTORING_ITERATIONS} reached for functional requirement {self.frid_context.frid}."
                )
            self.machine.dispatch(triggers.PROCEED_FRID_PROCESSING)

    def finish_refactoring_code(self):
        pass

    def start_testing_environment_preparation(self):
        if (
            self.prepare_environment_script is None
            or not self.conformance_tests_running_context.should_prepare_testing_environment
        ):
            self.machine.dispatch(triggers.MARK_TESTING_ENVIRONMENT_PREPARED)

    def start_conformance_tests_processing(self):
        console.info("Implementing conformance tests...")
        self.conformance_tests_running_context = ConformanceTestsRunningContext(
            current_testing_module_name=self.module_name,
            current_testing_frid=None,
            current_testing_frid_specifications=None,
            conformance_test_phase_index=0,
            fix_attempts=0,
            conformance_tests_json=self.conformance_tests.get_conformance_tests_json(self.module_name),
            conformance_tests_render_attempts=0,
            should_prepare_testing_environment=True,
        )

    def finish_conformance_tests_processing(self):
        self.conformance_tests_running_context = None

    def start_conformance_tests_for_frid(self):
        if self.conformance_tests_running_context.regenerating_conformance_tests:
            if self.verbose:
                console.info(
                    f"Recreating conformance tests for functional requirement {self.conformance_tests_running_context.current_testing_frid}."
                )

            existing_conformance_tests_folder = self.conformance_tests_running_context.get_conformance_tests_json(
                self.conformance_tests_running_context.current_testing_module_name
            ).pop(self.conformance_tests_running_context.current_testing_frid)

            file_utils.delete_folder(existing_conformance_tests_folder["folder_name"])

            self.conformance_tests_running_context.conformance_tests_render_attempts += 1
            self.conformance_tests_running_context.fix_attempts = 0
            self.conformance_tests_running_context.regenerating_conformance_tests = False
        else:
            # This block is now only executed for the main (last) module. This means that no conformance tests
            # postprocessing is taking place. Maybe it should?
            if (
                self.conformance_tests_running_context.current_testing_module_name == self.module_name
                and self.conformance_tests_running_context.current_testing_frid == self.frid_context.frid
            ):
                if not self.frid_context.specifications.get(
                    plain_spec.ACCEPTANCE_TESTS
                ) or self.conformance_tests_running_context.conformance_test_phase_index == len(
                    self.frid_context.specifications[plain_spec.ACCEPTANCE_TESTS]
                ):
                    self.machine.dispatch(triggers.MARK_ALL_CONFORMANCE_TESTS_PASSED)
                    return
                if self.conformance_tests_running_context.conformance_test_phase_index == 0:
                    self.conformance_tests_running_context.current_testing_frid_high_level_implementation_plan = None

                self.conformance_tests_running_context.conformance_test_phase_index += 1
                current_acceptance_tests = self.frid_context.specifications[plain_spec.ACCEPTANCE_TESTS][
                    : self.conformance_tests_running_context.conformance_test_phase_index
                ]
                self.conformance_tests_running_context.get_conformance_tests_json(
                    self.conformance_tests_running_context.current_testing_module_name
                )[self.frid_context.frid][plain_spec.ACCEPTANCE_TESTS] = current_acceptance_tests
                return

            if self.conformance_tests_running_context.current_testing_frid is None:
                self.conformance_tests_running_context = self.get_first_conformance_tests_running_context()
            else:
                self.conformance_tests_running_context = self.get_next_conformance_tests_running_context()

            if self.conformance_tests_running_context.current_testing_module_name == self.module_name:
                self.conformance_tests_running_context.current_testing_frid_specifications, _ = (
                    plain_spec.get_specifications_for_frid(
                        self.plain_source_tree, self.conformance_tests_running_context.current_testing_frid
                    )
                )
            else:
                self.conformance_tests_running_context.current_testing_frid_specifications = (
                    self.conformance_tests_running_context.get_conformance_tests_json(
                        self.conformance_tests_running_context.current_testing_module_name
                    )[self.conformance_tests_running_context.current_testing_frid]["functional_requirement"]
                )

            if self.conformance_tests_running_context.current_conformance_tests_exist():
                self.machine.dispatch(triggers.MARK_CONFORMANCE_TESTS_READY)

    def start_fixing_conformance_tests(self):
        self.conformance_tests_running_context.fix_attempts += 1

        if self.conformance_tests_running_context.fix_attempts >= MAX_CONFORMANCE_TEST_FIX_ATTEMPTS:
            if (
                self.conformance_tests_running_context.conformance_tests_render_attempts
                >= MAX_CONFORMANCE_TEST_RERENDER_ATTEMPTS
            ):
                error_msg = f"We've already tried to fix the issue by recreating the conformance tests but tests still fail. Please fix the issues manually. FRID: {self.frid_context.frid}, Render ID: {self.run_state.render_id}"
                self.dispatch_error(error_msg)
            else:
                self.conformance_tests_running_context.regenerating_conformance_tests = True
                self.machine.dispatch(triggers.MARK_REGENERATION_OF_CONFORMANCE_TESTS)

    def finish_fixing_conformance_tests(self):
        if self.verbose:
            console.info(
                f"Running conformance tests attempt {self.conformance_tests_running_context.fix_attempts + 1}."
            )
