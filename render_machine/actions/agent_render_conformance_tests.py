import os
from typing import Any

import plain_spec
from plain2code_console import console
from render_machine.actions.base_action import BaseAction
from render_machine.agent import agent_runner
from render_machine.agent.tool_executor import ToolExecutor
from render_machine.agent.tools import delete_file, edit_file, grep, list_files, ls_files, read_file, write_file
from render_machine.implementation_code_helpers import ImplementationCodeHelpers
from render_machine.render_context import RenderContext
from render_machine.render_types import AcceptanceTestPhase, TestExecutionPhase

RENDER_CONFORMANCE_TESTS_TOOLS = {
    "edit_file": edit_file,
    "write_file": write_file,
    "delete_file": delete_file,
    "read_file": read_file,
    "list_files": list_files,
    "ls_files": ls_files,
    "grep": grep,
}


class AgentRenderConformanceTests(BaseAction):
    SUCCESSFUL_OUTCOME = "conformance_test_rendered"

    def execute(self, render_context: RenderContext, _previous_action_payload: Any | None):
        if self._should_render_conformance_tests(render_context):
            return self._render_conformance_tests(render_context)
        else:
            return self._render_acceptance_test(render_context)

    def _should_render_conformance_tests(self, render_context: RenderContext) -> bool:
        ctx = render_context.conformance_tests_running_context
        if ctx.execution_phase == TestExecutionPhase.RUNNING_REGRESSION:
            return True
        if ctx.acceptance_test_phase == AcceptanceTestPhase.NOT_STARTED:
            return True
        if ctx.acceptance_test_phase == AcceptanceTestPhase.NOT_APPLICABLE:
            return True
        return False

    def _render_conformance_tests(self, render_context: RenderContext):
        if not render_context.conformance_tests_running_context.current_conformance_tests_exist():
            console.info("Agent implementing test requirements:")
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

            console.debug(f"Agent storing conformance test files in subfolder {conformance_tests_folder_name}/")

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

        all_acceptance_tests = render_context.frid_context.specifications.get(plain_spec.ACCEPTANCE_TESTS, [])

        task_params = {
            "specifications": self._build_specifications_text(render_context),
            "linked_resource_paths": self._get_linked_resource_paths(render_context),
            "acceptance_tests": all_acceptance_tests,
            "build_folder": render_context.build_folder,
            "conformance_tests_folder": conformance_tests_folder_name,
            "conformance_tests_script_path": render_context.conformance_tests_script or "",
            "prepare_environment_script_path": render_context.prepare_environment_script or "",
            "module_name": render_context.module_name,
        }

        tool_executor = ToolExecutor(available_tools=RENDER_CONFORMANCE_TESTS_TOOLS)
        agent_runner.run("render_conformance_tests", task_params, render_context, tool_executor)

        return self.SUCCESSFUL_OUTCOME, None

    def _render_acceptance_test(self, render_context: RenderContext):
        if plain_spec.ACCEPTANCE_TESTS not in render_context.frid_context.specifications:
            return self.SUCCESSFUL_OUTCOME, None

        conformance_tests_folder_name = (
            render_context.conformance_tests_running_context.get_current_conformance_test_folder_name()
        )

        _, conformance_tests_files_content = render_context.conformance_tests.fetch_existing_conformance_test_files(
            render_context.module_name,
            render_context.required_modules,
            render_context.conformance_tests_running_context.current_testing_module_name,
            conformance_tests_folder_name,
        )

        acceptance_test = render_context.frid_context.specifications[plain_spec.ACCEPTANCE_TESTS][
            render_context.conformance_tests_running_context.acceptance_tests_completed - 1
        ]

        console.info(f"Agent generating acceptance test:\n  {acceptance_test}")

        task_params = {
            "specifications": self._build_specifications_text(render_context),
            "linked_resource_paths": self._get_linked_resource_paths(render_context),
            "acceptance_test": acceptance_test,
            "existing_conformance_tests": conformance_tests_files_content,
            "build_folder": render_context.build_folder,
            "conformance_tests_folder": conformance_tests_folder_name,
            "conformance_tests_script_path": render_context.conformance_tests_script or "",
            "prepare_environment_script_path": render_context.prepare_environment_script or "",
            "module_name": render_context.module_name,
        }

        tool_executor = ToolExecutor(available_tools=RENDER_CONFORMANCE_TESTS_TOOLS)
        agent_runner.run("render_conformance_tests", task_params, render_context, tool_executor)

        return self.SUCCESSFUL_OUTCOME, None

    def _build_specifications_text(self, render_context: RenderContext) -> str:
        frid = render_context.frid_context.frid
        specifications, _ = plain_spec.get_specifications_for_frid(render_context.plain_source_tree, frid)

        parts = []
        if specifications.get(plain_spec.DEFINITIONS):
            parts.append(f"## Definitions\n{chr(10).join(specifications[plain_spec.DEFINITIONS])}")
        if specifications.get(plain_spec.NON_FUNCTIONAL_REQUIREMENTS):
            parts.append(
                f"## Non-Functional Requirements\n"
                f"{chr(10).join(specifications[plain_spec.NON_FUNCTIONAL_REQUIREMENTS])}"
            )
        if specifications.get(plain_spec.TEST_REQUIREMENTS):
            parts.append(f"## Test Requirements\n{chr(10).join(specifications[plain_spec.TEST_REQUIREMENTS])}")

        func_req_parts = self._build_functional_requirements_section(render_context)
        if func_req_parts:
            parts.append(func_req_parts)

        return "\n\n".join(parts)

    def _build_functional_requirements_section(self, render_context: RenderContext) -> str:
        required_modules_functionalities = render_context.get_required_modules_functionalities()
        current_module = render_context.module_name

        frid = render_context.frid_context.frid
        specifications, _ = plain_spec.get_specifications_for_frid(render_context.plain_source_tree, frid)
        current_module_func_reqs = specifications.get(plain_spec.FUNCTIONAL_REQUIREMENTS, [])

        if not required_modules_functionalities and not current_module_func_reqs:
            return ""

        sections = ["## Functional Requirements\n"]

        for module_name, func_list in required_modules_functionalities.items():
            sections.append(
                f"### Module: {module_name} (Already Implemented, for context):\n{chr(10).join(func_list)}"
            )

        if current_module_func_reqs:
            if len(current_module_func_reqs) > 1:
                sections.append(
                    f"### Module: {current_module} (Already Implemented, for context):\n"
                    f"{chr(10).join(current_module_func_reqs[:-1])}\n"
                )
                sections.append(
                    f"### Module: {current_module} (Currently Being Tested):\n{current_module_func_reqs[-1]}"
                )
            else:
                sections.append(
                    f"### Module: {current_module} (Currently Being Tested):\n{current_module_func_reqs[0]}"
                )

        return "\n\n".join(sections)

    def _get_linked_resource_paths(self, render_context: RenderContext) -> list[str]:
        linked_resources = render_context.frid_context.linked_resources
        if not linked_resources:
            return []
        return list(linked_resources.keys())
