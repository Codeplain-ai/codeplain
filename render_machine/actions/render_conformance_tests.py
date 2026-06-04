import os
from typing import Any

import file_utils
import plain_spec
from memory_management import MemoryManager
from plain2code_console import console
from render_machine.actions.base_action import BaseAction
from render_machine.implementation_code_helpers import ImplementationCodeHelpers
from render_machine.render_context import RenderContext
from render_machine.render_types import AcceptanceTestPhase, TestExecutionPhase


class RenderConformanceTests(BaseAction):
    SUCCESSFUL_OUTCOME = "conformance_test_rendered"

    def execute(self, render_context: RenderContext, _previous_action_payload: Any | None):
        if self._should_render_conformance_tests(render_context):
            return self._render_conformance_tests(render_context)
        else:
            return self._render_acceptance_test(render_context)

    def _should_render_conformance_tests(self, render_context: RenderContext) -> bool:
        """Check if we should render full conformance tests (first time) vs just acceptance tests (incremental)."""

        ctx = render_context.conformance_tests_running_context

        # If we're in regression phase, tests already exist - don't render anything new
        if ctx.execution_phase == TestExecutionPhase.RUNNING_REGRESSION:
            return True  # Return True to skip rendering (conformance tests already exist)

        # Render full conformance tests when:
        # 1. NOT_STARTED: First render, before acceptance tests begin
        if ctx.acceptance_test_phase == AcceptanceTestPhase.NOT_STARTED:
            return True

        # 2. NOT_APPLICABLE: No acceptance tests defined, always use full conformance test path
        if ctx.acceptance_test_phase == AcceptanceTestPhase.NOT_APPLICABLE:
            # Return True so _render_conformance_tests() is called, which will skip if tests already exist
            return True

        # 3. All other cases (IN_PROGRESS, COMPLETED): Render acceptance tests incrementally
        return False

    def _render_conformance_tests(self, render_context: RenderContext):
        # Check if tests already exist (e.g., during regression) - if so, skip rendering
        if not render_context.conformance_tests_running_context.current_conformance_tests_exist():
            console.info("Implementing test requirements:")
            console.print_list(
                render_context.conformance_tests_running_context.current_testing_frid_specifications[
                    plain_spec.TEST_REQUIREMENTS
                ],
                style=console.INFO_STYLE,
            )
            fr_subfolder_name = render_context.codeplain_api.generate_folder_name_from_functional_requirement(
                frid=render_context.conformance_tests_running_context.current_testing_frid,
                module_name=render_context.conformance_tests_running_context.current_testing_module_name,
                functional_requirement=render_context.conformance_tests_running_context.current_testing_frid_specifications[
                    plain_spec.FUNCTIONAL_REQUIREMENTS
                ][
                    -1
                ],
                existing_folder_names=render_context.conformance_tests.fetch_existing_conformance_test_folder_names(
                    render_context.conformance_tests_running_context.current_testing_module_name
                ),
                run_state=render_context.run_state,
            )

            conformance_tests_folder_name = os.path.join(
                render_context.conformance_tests.get_module_conformance_tests_folder(render_context.module_name),
                fr_subfolder_name,
            )

            console.debug(f"Storing conformance test files in subfolder {conformance_tests_folder_name}/")

            render_context.conformance_tests_running_context.get_conformance_tests_json(
                render_context.conformance_tests_running_context.current_testing_module_name
            )[render_context.conformance_tests_running_context.current_testing_frid] = {
                "folder_name": conformance_tests_folder_name,
                "functional_requirement": render_context.frid_context.specifications[
                    plain_spec.FUNCTIONAL_REQUIREMENTS
                ][-1],
            }
        else:
            conformance_tests_folder_name = (
                render_context.conformance_tests_running_context.get_current_conformance_test_folder_name()
            )

        _, existing_files_content = ImplementationCodeHelpers.fetch_existing_files(render_context.build_folder)
        _, memory_files_content = MemoryManager.fetch_memory_files(render_context.memory_manager.memory_folder)
        tmp_resources_list = []
        plain_spec.collect_linked_resources(
            render_context.plain_source_tree,
            tmp_resources_list,
            [
                plain_spec.DEFINITIONS,
                plain_spec.TEST_REQUIREMENTS,
                plain_spec.FUNCTIONAL_REQUIREMENTS,
            ],
            False,
            render_context.frid_context.frid,
        )
        console.print_resources(tmp_resources_list, render_context.frid_context.linked_resources)

        console.print_files(
            "Files sent as input for generating conformance tests:",
            render_context.build_folder,
            existing_files_content,
            style=console.INPUT_STYLE,
        )

        all_acceptance_tests = render_context.frid_context.specifications.get(plain_spec.ACCEPTANCE_TESTS, [])

        response_files, implementation_plan_summary = render_context.codeplain_api.render_conformance_tests(
            render_context.frid_context.frid,
            render_context.conformance_tests_running_context.current_testing_frid,
            render_context.plain_source_tree,
            render_context.frid_context.linked_resources,
            existing_files_content,
            memory_files_content,
            render_context.module_name,
            render_context.get_required_modules_functionalities(),
            conformance_tests_folder_name,
            render_context.conformance_tests_running_context.get_conformance_tests_json(
                render_context.conformance_tests_running_context.current_testing_module_name
            ),
            all_acceptance_tests,
            run_state=render_context.run_state,
            is_reimplementation=render_context.is_rerender,
        )

        render_context.conformance_tests_running_context.current_testing_frid_high_level_implementation_plan = (
            implementation_plan_summary
        )

        file_utils.store_response_files(conformance_tests_folder_name, response_files, [])

        console.print_files(
            "Conformance test files generated:",
            conformance_tests_folder_name,
            response_files,
            style=console.OUTPUT_STYLE,
        )

        return self.SUCCESSFUL_OUTCOME, None

    def _render_acceptance_test(self, render_context: RenderContext):
        if plain_spec.ACCEPTANCE_TESTS not in render_context.frid_context.specifications:
            # If there are no acceptance tests defined, continue.
            return self.SUCCESSFUL_OUTCOME, None

        _, existing_files_content = ImplementationCodeHelpers.fetch_existing_files(render_context.build_folder)
        _, memory_files_content = MemoryManager.fetch_memory_files(render_context.memory_manager.memory_folder)
        (
            conformance_tests_files,
            conformance_tests_files_content,
        ) = render_context.conformance_tests.fetch_existing_conformance_test_files(
            render_context.module_name,
            render_context.required_modules,
            render_context.conformance_tests_running_context.current_testing_module_name,
            render_context.conformance_tests_running_context.get_current_conformance_test_folder_name(),
        )

        # Get the current acceptance test being rendered (completed count is 1-based)
        acceptance_test = render_context.frid_context.specifications[plain_spec.ACCEPTANCE_TESTS][
            render_context.conformance_tests_running_context.acceptance_tests_completed - 1
        ]

        console.info(f"Generating acceptance test:\n  {acceptance_test}")

        response_files = render_context.codeplain_api.render_acceptance_tests(
            render_context.frid_context.frid,
            render_context.plain_source_tree,
            render_context.frid_context.linked_resources,
            existing_files_content,
            memory_files_content,
            conformance_tests_files_content,
            render_context.module_name,
            render_context.get_required_modules_functionalities(),
            acceptance_test,
            run_state=render_context.run_state,
        )
        conformance_tests_folder_name = (
            render_context.conformance_tests_running_context.get_current_conformance_test_folder_name()
        )

        file_utils.store_response_files(conformance_tests_folder_name, response_files, conformance_tests_files)
        console.print_files(
            f"Conformance test files in folder {conformance_tests_folder_name} updated:",
            conformance_tests_folder_name,
            response_files,
            style=console.OUTPUT_STYLE,
        )
        return self.SUCCESSFUL_OUTCOME, None
