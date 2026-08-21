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
    FridContext,
    ModuleConformanceSuite,
    ModuleConformanceTestsRunningContext,
    ScriptExecutionHistory,
    UnitTestsRunningContext,
)

MAX_UNITTEST_FIX_ATTEMPTS = 20

# A module conformance suite covers every functionality of the module, so its fix budget scales with
# the number of functionalities rather than being the flat per-functionality budget.
MAX_MODULE_CONFORMANCE_TEST_FIX_ATTEMPTS_BASE = 20
MAX_MODULE_CONFORMANCE_TEST_FIX_ATTEMPTS_PER_FRID = 10
MAX_MODULE_CONFORMANCE_TEST_FIX_ATTEMPTS_CAP = 120


class RenderContext:
    def __init__(
        self,
        codeplain_api,
        memory_manager,
        plain_module: PlainModule,
        build_folder: str,
        build_dest: str,
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
        self.module_conformance_tests_running_context: Optional[ModuleConformanceTestsRunningContext] = None

        # Initialize conformance tests utilities. The resolver lets a required module that ships
        # as a "<module>.module" archive resolve to its scratch extraction (via materialize())
        # rather than the non-existent default plain_modules/<module>/tests path.
        def _resolve_module_tests_folder(module_name: str) -> Optional[str]:
            if module_name == plain_module.module_name:
                return plain_module.module_conformance_tests_folder
            for required_module in plain_module.all_required_modules:
                if required_module.module_name == module_name:
                    return required_module.module_conformance_tests_folder
            return None

        self.conformance_tests = ConformanceTests(
            modules_base_folder=plain_module.build_folder,
            conformance_tests_definition_file_name=CONFORMANCE_TESTS_DEFINITION_FILE_NAME,
            resolve_module_tests_folder=_resolve_module_tests_folder,
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
            module_conformance_tests_running_context=(
                deepcopy(self.module_conformance_tests_running_context)
                if self.module_conformance_tests_running_context
                else None
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
        self.run_state.increment_rendered_functionalities()

    def should_run_unit_tests(self) -> bool:
        return self.unittests_script is not None

    def should_run_conformance_tests(self) -> bool:
        """Whether the module conformance testing phase runs at all.

        Conformance tests are always scoped to the whole module: they are planned, implemented, run
        and fixed once every functionality of the module has been implemented.
        """
        return self.conformance_tests_script is not None

    def start_unittests_processing(self):
        self.unit_tests_running_context = UnitTestsRunningContext(fix_attempts=0)
        self.run_state.increment_unittest_batch_id()

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

    def _on_unit_test_limit_exceeded_in_refactoring(self):
        git_utils.revert_changes(self.build_folder)
        self.machine.dispatch(triggers.START_NEW_REFACTORING_ITERATION)

    # ========== Module-scoped conformance testing ==========
    #
    # Where per-functionality conformance testing walks functionality by functionality inside
    # implementingFrid, this is a single root-level phase entered once every functionality of the
    # module has been implemented. It plans one test suite covering the whole module, implements the
    # plan in batches, and then runs every suite in scope - the suites of the required modules as
    # regression, then this module's own.

    def _build_module_conformance_suites(self) -> list[ModuleConformanceSuite]:
        """The suites that have to pass before the module is considered done, in the order they run.

        A required module may have been rendered under either scope, so it contributes either its one
        module suite or one suite per functionality. That keeps regression working across a requires
        chain whose modules were not all rendered with the same conformance scope.
        """
        suites = []

        for required_module in self.required_modules or []:
            module_suite_folder_name = self.conformance_tests.get_module_suite_folder_name(required_module.module_name)
            if module_suite_folder_name is not None:
                suites.append(
                    ModuleConformanceSuite(
                        module_name=required_module.module_name,
                        folder_name=module_suite_folder_name,
                        is_own_module=False,
                    )
                )
                continue

            required_module_conformance_tests = self.conformance_tests.get_conformance_tests_json(
                required_module.module_name
            )
            for frid, entry in required_module_conformance_tests.items():
                if not isinstance(entry, dict) or "folder_name" not in entry:
                    continue
                suites.append(
                    ModuleConformanceSuite(
                        module_name=required_module.module_name,
                        folder_name=entry["folder_name"],
                        is_own_module=False,
                        frid=frid,
                    )
                )

        suites.append(
            ModuleConformanceSuite(
                module_name=self.module_name,
                folder_name=self.conformance_tests.get_module_suite_folder_name(self.module_name),
                is_own_module=True,
            )
        )

        return suites

    def start_module_conformance_tests_processing(self):
        console.info(f"Implementing conformance tests for module {self.module_name}...")
        self.module_conformance_tests_running_context = ModuleConformanceTestsRunningContext(
            module_name=self.module_name,
            suites=self._build_module_conformance_suites(),
        )
        self.module_conformance_tests_running_context.acceptance_tests = self.get_module_acceptance_tests()
        self.run_state.current_frid = plain_spec.MODULE_SCOPE_FRID

    def finish_module_conformance_tests_processing(self):
        self.module_conformance_tests_running_context = None

    def get_max_module_conformance_test_fix_attempts(self) -> int:
        """How many fix attempts a module suite gets before the render is failed.

        A module suite covers every functionality of the module, so it can fail in more ways than a
        per-functionality suite can. The budget therefore grows with the number of functionalities,
        up to a cap that keeps a hopeless run from going on indefinitely.
        """
        number_of_frids = len(list(plain_spec.get_frids(self.plain_source_tree)))

        return min(
            MAX_MODULE_CONFORMANCE_TEST_FIX_ATTEMPTS_BASE
            + MAX_MODULE_CONFORMANCE_TEST_FIX_ATTEMPTS_PER_FRID * number_of_frids,
            MAX_MODULE_CONFORMANCE_TEST_FIX_ATTEMPTS_CAP,
        )

    def get_required_modules_conformance_tests(self) -> dict:
        """The conformance test summaries of the required modules, keyed by module name.

        The module test plan is grounded in these so that it does not re-test what the modules this
        one builds upon already cover.
        """
        required_modules_conformance_tests = {}

        for required_module in self.required_modules or []:
            test_summaries = self.conformance_tests.get_module_conformance_tests_summary(required_module.module_name)
            if not test_summaries:
                # A required module rendered under the per-functionality scope has one summary per
                # functionality rather than one for the module.
                test_summaries = []
                for entry in self.conformance_tests.get_conformance_tests_json(required_module.module_name).values():
                    if isinstance(entry, dict) and entry.get("test_summary"):
                        test_summaries.extend(entry["test_summary"])

            if test_summaries:
                required_modules_conformance_tests[required_module.module_name] = test_summaries

        return required_modules_conformance_tests

    def get_module_acceptance_tests(self) -> list[tuple[str, str]]:
        """Every acceptance test of the module as (frid, acceptance test), in functionality order.

        The owning functionality is kept because an acceptance test specifies that functionality: it
        is what the acceptance test is rendered against, even though the suite it lands in covers the
        whole module.
        """
        acceptance_tests = []

        for frid in plain_spec.get_frids(self.plain_source_tree):
            specifications, _ = plain_spec.get_specifications_for_frid(self.plain_source_tree, frid)
            for acceptance_test in specifications.get(plain_spec.ACCEPTANCE_TESTS, []):
                acceptance_tests.append((frid, acceptance_test))

        return acceptance_tests

    def _on_unit_test_limit_exceeded_in_module_conformance_tests(self):
        error_msg = (
            f"Failed to adjust the unit tests of module {self.module_name} after the implementation code was "
            f"updated while fixing the conformance tests of the module."
        )
        self.dispatch_error(error_msg)

    def start_render_completed(self):
        self.run_state.set_render_succeeded(True)

    def start_render_failed(self):
        self.run_state.set_render_succeeded(False)
