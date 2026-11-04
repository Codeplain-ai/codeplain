import os
import re
from typing import Any, Optional

import file_utils
import plain_spec
from memory_management import MemoryManager
from plain2code_console import console
from render_machine.actions.base_action import BaseAction
from render_machine.implementation_code_helpers import ImplementationCodeHelpers
from render_machine.render_context import RenderContext
from render_machine.render_types import AcceptanceTestPhase, TestExecutionPhase

# Bound on the reference conformance tests sent for generating new ones (see _reference_conformance_tests).
MAX_REFERENCE_TESTS_CHARS = 40_000


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

    @staticmethod
    def _reference_conformance_tests(render_context: RenderContext, target_folder: str) -> dict[str, str]:
        """The files of the most recent earlier functionality's conformance tests (they pass, or the render would
        not have moved on), as a reference for setting up new ones: of the current module if it has any, otherwise
        of the closest required module. Files by full path, smallest first, within MAX_REFERENCE_TESTS_CHARS."""
        running = render_context.conformance_tests_running_context
        candidates: list[dict] = []
        if running.conformance_tests_json_has_module_populated(render_context.module_name):
            candidates.append(running.get_conformance_tests_json(render_context.module_name))
        for module in reversed(render_context.plain_module.all_required_modules):
            candidates.append(render_context.conformance_tests.get_conformance_tests_json(module.module_name))
        target = os.path.normpath(os.path.abspath(target_folder))
        for tests_json in candidates:
            for entry in reversed(list(tests_json.values())):
                folder = os.path.normpath(os.path.abspath(entry.get("folder_name") or ""))
                if folder == target or not os.path.isdir(folder):
                    continue
                files = _text_files(folder, MAX_REFERENCE_TESTS_CHARS)
                if files:
                    return files
        return {}

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
            existing_folder_names = render_context.conformance_tests.fetch_existing_conformance_test_folder_names(
                render_context.conformance_tests_running_context.current_testing_module_name
            )
            fr_subfolder_name = tests_folder_name(
                str(render_context.conformance_tests_running_context.current_testing_frid), existing_folder_names
            )
            if fr_subfolder_name is None:
                fr_subfolder_name = render_context.codeplain_api.generate_folder_name_from_functional_requirement(
                    frid=render_context.conformance_tests_running_context.current_testing_frid,
                    module_name=render_context.conformance_tests_running_context.current_testing_module_name,
                    functional_requirement=render_context.conformance_tests_running_context.current_testing_frid_specifications[
                        plain_spec.FUNCTIONAL_REQUIREMENTS
                    ][
                        -1
                    ],
                    existing_folder_names=existing_folder_names,
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

        reference_conformance_tests = self._reference_conformance_tests(render_context, conformance_tests_folder_name)
        if reference_conformance_tests:
            console.debug(
                "Reference conformance tests sent for generating conformance tests: "
                + ", ".join(sorted(reference_conformance_tests))
            )

        response_files, traceability = render_context.codeplain_api.render_conformance_tests(
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
            run_state=render_context.run_state,
            reference_conformance_tests=reference_conformance_tests,
        )

        self._warn_about_untraced_scenarios(render_context, traceability)

        file_utils.store_response_files(conformance_tests_folder_name, response_files, [])

        console.print_files(
            "Conformance test files generated:",
            conformance_tests_folder_name,
            response_files,
            style=console.OUTPUT_STYLE,
        )

        return self.SUCCESSFUL_OUTCOME, None

    @staticmethod
    def _warn_about_untraced_scenarios(render_context: RenderContext, traceability: dict) -> None:
        """Warn about a scenario with no test, a test that implements no scenario, and a scenario
        with more than one test.

        Both test renders report the same traceability, so both are checked the same way. A render
        that writes a test the specification did not ask for, or leaves one it did ask for unwritten,
        must say so instead of reporting success.
        """
        frid = render_context.conformance_tests_running_context.current_testing_frid

        missing = traceability.get("missing") or []
        if missing:
            console.warning(f"No conformance test was generated for {frid} scenarios: {', '.join(missing)}.")

        extra = traceability.get("extra") or []
        if extra:
            console.warning(f"Conformance tests of {frid} that implement no scenario: {', '.join(extra)}.")

        duplicated = traceability.get("duplicated") or []
        if duplicated:
            console.warning(
                f"More than one conformance test of {frid} implements the scenarios: {', '.join(duplicated)}."
            )

    def _render_acceptance_test(self, render_context: RenderContext):
        if plain_spec.ACCEPTANCE_TESTS not in render_context.frid_context.specifications:
            # If there are no acceptance tests defined, continue.
            return self.SUCCESSFUL_OUTCOME, None

        ctx = render_context.conformance_tests_running_context

        # Get the current acceptance scenario being rendered (completed count is 1-based)
        scenario = render_context.frid_context.specifications[plain_spec.ACCEPTANCE_TESTS][
            ctx.acceptance_tests_completed - 1
        ]

        # This action is entered through more than one path, and the counter above belongs to the
        # state machine, not to the action. A path that enters it again without the counter moving
        # asks for a scenario that already has a test. Rendering it again costs a call and lets the
        # render write a second test for the one scenario.
        if scenario["id"] in ctx.rendered_acceptance_scenarios:
            console.warning(f"The acceptance scenario {scenario['id']} already has a test. It is not rendered again.")
            return self.SUCCESSFUL_OUTCOME, None

        _, existing_files_content = ImplementationCodeHelpers.fetch_existing_files(render_context.build_folder)
        _, memory_files_content = MemoryManager.fetch_memory_files(render_context.memory_manager.memory_folder)
        (
            conformance_tests_files,
            conformance_tests_files_content,
        ) = render_context.conformance_tests.fetch_existing_conformance_test_files(
            render_context.module_name,
            render_context.required_modules,
            ctx.current_testing_module_name,
            ctx.get_current_conformance_test_folder_name(),
        )

        console.info(f"Generating acceptance test for scenario {scenario['id']}.")

        response_files, traceability = render_context.codeplain_api.render_acceptance_tests(
            render_context.frid_context.frid,
            render_context.plain_source_tree,
            render_context.frid_context.linked_resources,
            existing_files_content,
            memory_files_content,
            conformance_tests_files_content,
            render_context.module_name,
            render_context.get_required_modules_functionalities(),
            scenario["id"],
            run_state=render_context.run_state,
        )

        self._warn_about_untraced_scenarios(render_context, traceability)
        ctx.rendered_acceptance_scenarios.append(scenario["id"])

        conformance_tests_folder_name = ctx.get_current_conformance_test_folder_name()

        file_utils.store_response_files(conformance_tests_folder_name, response_files, conformance_tests_files)
        console.print_files(
            f"Conformance test files in folder {conformance_tests_folder_name} updated:",
            conformance_tests_folder_name,
            response_files,
            style=console.OUTPUT_STYLE,
        )
        return self.SUCCESSFUL_OUTCOME, None


def _text_files(folder: str, max_chars: int) -> dict[str, str]:
    """The text files of `folder` by full path, smallest first, within `max_chars`."""
    contents = file_utils.get_existing_files_content(folder, file_utils.list_all_text_files(folder))
    selected, total = {}, 0
    for name in sorted(contents, key=lambda n: (len(contents[n]), n)):
        if total + len(contents[name]) > max_chars:
            break
        selected[os.path.join(folder, name)] = contents[name]
        total += len(contents[name])
    return dict(sorted(selected.items()))


def tests_folder_name(frid: str, existing_folder_names: list[str]) -> Optional[str]:
    """The conformance tests folder name of a functionality: its ID in snake_case (func-ids are written to be read,
    e.g. `register-account`), with a numeric suffix if the name is taken. None for an ID without letters (e.g. a
    numbered one), whose name the server then generates from the functionality's text."""
    base = re.sub(r"[^a-z0-9]+", "_", str(frid).lower()).strip("_")
    if not re.search(r"[a-z]", base):
        return None
    taken = {os.path.basename(os.path.normpath(name)) for name in existing_folder_names}
    name, number = base, 2
    while name in taken:
        name, number = f"{base}_{number}", number + 1
    return name
