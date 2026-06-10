import os
from typing import Any

import plain_spec
from plain2code_console import console
from render_machine.actions.base_action import BaseAction
from render_machine.agent import agent_runner
from render_machine.agent.tool_executor import ToolExecutor
from render_machine.agent.tools import (
    delete_file,
    edit_file,
    grep,
    list_files,
    ls_files,
    read_file,
    write_file,
)
from render_machine.render_context import RenderContext


class AgentFixConformanceTest(BaseAction):
    FIX_READY_FOR_REVIEW = "fix_ready_for_review"

    def execute(self, render_context: RenderContext, previous_action_payload: Any | None):
        # Gather all feedback from previous iterations
        previous_agent_summary = (
            previous_action_payload.get("previous_agent_summary", "") if previous_action_payload else ""
        )
        review_rejection_feedback = (
            previous_action_payload.get("review_rejection_feedback", "") if previous_action_payload else ""
        )
        prepare_environment_failure = (
            previous_action_payload.get("prepare_environment_failure", "") if previous_action_payload else ""
        )

        console.info(
            f"Agent fixing conformance test for functionality "
            f"{render_context.conformance_tests_running_context.current_testing_frid} "
            f"in module {render_context.conformance_tests_running_context.current_testing_module_name}."
        )

        # Reset tracker so this fix attempt starts with a clean slate
        render_context.conformance_tests_running_context.reset_file_change_tracker()

        conformance_test_folder = self._get_conformance_test_folder(render_context)

        # Build context
        specifications = self._build_specifications_text(render_context)
        acceptance_tests = self._build_acceptance_tests_text(render_context)

        # Only provide code editing tools (no test/review tools)
        tools = {
            "edit_file": edit_file,
            "write_file": write_file,
            "delete_file": delete_file,
            "read_file": read_file,
            "list_files": list_files,
            "ls_files": ls_files,
            "grep": grep,
        }

        # Get test output file path from context (set by RunConformanceTests)
        test_output_file = render_context.conformance_tests_running_context.test_output_file_path or ""
        linked_resource_paths = self._get_linked_resource_paths(render_context)

        tool_executor = ToolExecutor(available_tools=tools)

        # Check if this is a continuation of an existing fix session
        session_id = render_context.conformance_tests_running_context.fix_agent_session_id

        if session_id is None:
            # First attempt - start new agent session
            task_params = {
                "specifications": specifications,
                "test_output_file": test_output_file,
                "linked_resource_paths": linked_resource_paths,
                "acceptance_tests": acceptance_tests,
                "build_folder": render_context.build_folder,
                "conformance_tests_folder": conformance_test_folder,
                "conformance_tests_script_path": render_context.conformance_tests_script or "",
                "prepare_environment_script_path": render_context.prepare_environment_script or "",
                "module_name": render_context.module_name,
                "keep_session_alive": True,  # Mark this as a persistent session
            }
            response = agent_runner.run(
                "fix_conformance_tests",
                task_params,
                render_context,
                tool_executor,
                keep_session_alive=True,  # Keep session alive for future continuations
            )

            # Store session ID for future attempts
            if "session_id" in response:
                render_context.conformance_tests_running_context.fix_agent_session_id = response["session_id"]
        else:
            # Subsequent attempt - continue existing session with new information
            additional_context = self._build_continuation_message(
                test_output_file=test_output_file,
                review_rejection_feedback=review_rejection_feedback,
                prepare_environment_failure=prepare_environment_failure,
            )

            try:
                response = agent_runner.continue_session(
                    session_id=session_id,
                    additional_context=additional_context,
                    render_context=render_context,
                    tool_executor=tool_executor,
                )
            except Exception as e:
                # If session not found (404) or expired, start a fresh session
                # This happens when the agent hits max turns or session expires
                if "404" in str(e) or "not found" in str(e).lower():
                    console.warning(
                        f"Previous fix session expired or hit max turns. Starting fresh session with accumulated context."
                    )
                    render_context.conformance_tests_running_context.fix_agent_session_id = None

                    # Start new session with full context (includes previous failure info)
                    task_params = {
                        "specifications": specifications,
                        "test_output_file": test_output_file,
                        "linked_resource_paths": linked_resource_paths,
                        "acceptance_tests": acceptance_tests,
                        "build_folder": render_context.build_folder,
                        "conformance_tests_folder": conformance_test_folder,
                        "conformance_tests_script_path": render_context.conformance_tests_script or "",
                        "prepare_environment_script_path": render_context.prepare_environment_script or "",
                        "module_name": render_context.module_name,
                        "keep_session_alive": True,
                        # Include context from previous attempts (if available)
                        "previous_agent_summary": previous_agent_summary if previous_agent_summary else None,
                        "review_rejection_feedback": review_rejection_feedback if review_rejection_feedback else None,
                        "prepare_environment_failure": (
                            prepare_environment_failure if prepare_environment_failure else None
                        ),
                    }
                    response = agent_runner.run(
                        "fix_conformance_tests",
                        task_params,
                        render_context,
                        tool_executor,
                        keep_session_alive=True,
                    )

                    # Store new session ID
                    if "session_id" in response:
                        render_context.conformance_tests_running_context.fix_agent_session_id = response["session_id"]
                else:
                    # Re-raise other errors
                    raise

        # Extract agent's summary message
        agent_summary = response.get("result", "") if response.get("status") == "completed" else ""

        return self.FIX_READY_FOR_REVIEW, {
            "agent_summary": agent_summary,
            "specifications": specifications,
            "acceptance_tests": acceptance_tests,
            "conformance_test_folder": conformance_test_folder,
        }

    def _get_conformance_test_folder(self, render_context: RenderContext) -> str:
        ctx = render_context.conformance_tests_running_context
        if render_context.module_name == ctx.current_testing_module_name:
            return ctx.get_current_conformance_test_folder_name()
        folder, _ = render_context.conformance_tests.get_source_conformance_test_folder_name(
            render_context.module_name,
            render_context.required_modules,
            ctx.current_testing_module_name,
            ctx.get_current_conformance_test_folder_name(),
        )
        return folder

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
            parts.append(f"## Test Requirements\n" f"{chr(10).join(specifications[plain_spec.TEST_REQUIREMENTS])}")

        # Build functional requirements section with all modules
        func_req_parts = self._build_functional_requirements_section(render_context)
        if func_req_parts:
            parts.append(func_req_parts)

        return "\n\n".join(parts)

    def _build_functional_requirements_section(self, render_context: RenderContext) -> str:
        """Build functional requirements section showing all modules and their functionalities."""
        # Get functionalities from required modules
        required_modules_functionalities = render_context.get_required_modules_functionalities()
        current_module = render_context.module_name

        # Get current module's functionalities from specifications
        frid = render_context.frid_context.frid
        specifications, _ = plain_spec.get_specifications_for_frid(render_context.plain_source_tree, frid)
        current_module_func_reqs = specifications.get(plain_spec.FUNCTIONAL_REQUIREMENTS, [])

        # If no functionalities at all, return empty
        if not required_modules_functionalities and not current_module_func_reqs:
            return ""

        sections = ["## Functional Requirements\n"]

        # First, add required modules (all already implemented)
        for module_name, func_list in required_modules_functionalities.items():
            sections.append(
                f"### Module: {module_name} (Already Implemented, for context):\n" f"{chr(10).join(func_list)}"
            )

        # Then, add current module functionalities
        if current_module_func_reqs:
            if len(current_module_func_reqs) > 1:
                # Split into implemented and current
                sections.append(
                    f"### Module: {current_module} (Already Implemented, for context):\n"
                    f"{chr(10).join(current_module_func_reqs[:-1])}\n"
                )
                sections.append(
                    f"### Module: {current_module} (Currently Being Implemented):\n" f"{current_module_func_reqs[-1]}"
                )
            else:
                # Only one functionality (the current one)
                sections.append(
                    f"### Module: {current_module} (Currently Being Implemented):\n" f"{current_module_func_reqs[0]}"
                )

        return "\n\n".join(sections)

    def _build_acceptance_tests_text(self, render_context: RenderContext) -> str:
        acceptance_tests = render_context.conformance_tests_running_context.get_current_acceptance_tests()
        if not acceptance_tests:
            return ""
        return "\n".join(acceptance_tests)

    def _read_conformance_tests_script(self, render_context: RenderContext) -> str:
        script_path = os.path.normpath(render_context.conformance_tests_script)
        if not os.path.exists(script_path):
            return ""
        with open(script_path, "r", encoding="utf-8") as f:
            return f.read()

    def _get_linked_resource_paths(self, render_context: RenderContext) -> list[str]:
        """Get list of linked resource paths (not content) for the agent to read if needed."""
        linked_resources = render_context.frid_context.linked_resources
        if not linked_resources:
            return []
        return list(linked_resources.keys())

    def _build_continuation_message(
        self, test_output_file: str, review_rejection_feedback: str, prepare_environment_failure: str
    ) -> str:
        """Build a message to continue the agent session with new test results and feedback."""
        parts = []

        # Review feedback
        if review_rejection_feedback:
            parts.append("The reviewer examined your fix and evaluated it according to the engineering integrity guidelines. The reviewer rejected your fix with this reviewer feedback:\n\n{review_rejection_feedback}\n")
            parts.append("Please address the reviewer's concerns and try again.\n")

        # Environment preparation result
        if prepare_environment_failure:
            parts.append(f"The environment preparation (build/compile) failed:\n\n{prepare_environment_failure}\n")
            parts.append(
                "Please fix the build/compilation issues before the tests can run. "
                "The test output file below may be outdated.\n"
            )

        # Test result
        if test_output_file:
            parts.append(
                f"Your fix was evaluated by running the conformance tests using the conformance tests script, but the tests still failed. "
                f"The test script output was saved to a file which is available at: {test_output_file}\n\n"
            )
        else:
            parts.append("Your fix was evaluating by running the conformance tests using the conformance tests script, but the tests still failed, and no test output file is available.\n")

        parts.append("\nFix the failing tests:\n")
        parts.append("1. Thoroughly read the:\n")
        if test_output_file:
            parts.append(f"  - The test output file at: {test_output_file}\n")
        if prepare_environment_failure:
            parts.append(f"  - The environment preparation failure\n")
        if review_rejection_feedback:
            parts.append(f"  - The reviewer feedback\n")
        parts.append("2. Locate and read the failing test code and the implementation code that is being tested. Make sure you understand the root cause of the failing tests.\n")
        parts.append("3. Implement a fix that addresses the root cause.\n")
        parts.append("4. Verify the fix by reading the edited files to ensure the fix is correct.\n")
        parts.append("5. Provide a summary of what you changed and why.\n")

        return "\n".join(parts)
