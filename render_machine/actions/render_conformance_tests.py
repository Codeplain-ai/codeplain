import os
from typing import Any

import file_utils
import plain_spec
from memory_management import MemoryManager
from plain2code_console import console
from render_machine.actions.base_action import BaseAction
from render_machine.implementation_code_helpers import ImplementationCodeHelpers
from render_machine.render_context import RenderContext


class RenderConformanceTests(BaseAction):
    SUCCESSFUL_OUTCOME = "conformance_test_rendered"

    def execute(self, render_context: RenderContext, _previous_action_payload: Any | None):
        if self._should_render_conformance_tests(render_context):
            return self._render_conformance_tests(render_context)
        else:
            return self._render_acceptance_test(render_context)

    def _should_render_conformance_tests(self, render_context: RenderContext) -> bool:
        return render_context.conformance_tests_running_context.conformance_test_phase_index == 0

    def _render_conformance_tests(self, render_context: RenderContext):
        if render_context.verbose:
            console.info("Implementing test requirements:")
            console.print_list(
                render_context.conformance_tests_running_context.current_testing_frid_specifications[
                    plain_spec.TEST_REQUIREMENTS
                ],
                style=console.INFO_STYLE,
            )

        if not render_context.conformance_tests_running_context.current_conformance_tests_exist():
            fr_subfolder_name = render_context.codeplain_api.generate_folder_name_from_functional_requirement(
                frid=render_context.conformance_tests_running_context.current_testing_frid,
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

            if render_context.verbose:
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
        if render_context.verbose:
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
        )

        render_context.conformance_tests_running_context.current_testing_frid_high_level_implementation_plan = (
            implementation_plan_summary
        )

        file_utils.store_response_files(conformance_tests_folder_name, response_files, [])

        if render_context.verbose:
            console.print_files(
                "Conformance test files generated:",
                conformance_tests_folder_name,
                response_files,
                style=console.OUTPUT_STYLE,
            )

        return self.SUCCESSFUL_OUTCOME, None

    def _render_acceptance_test(self, render_context: RenderContext):
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

        acceptance_test = render_context.frid_context.specifications[plain_spec.ACCEPTANCE_TESTS][
            render_context.conformance_tests_running_context.conformance_test_phase_index - 1
        ]

        if render_context.verbose:
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
