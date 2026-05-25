"""
State machine configuration for the code rendering process.

This module defines the hierarchical state machine structure, transitions, and action mappings
used by the CodeRenderer to orchestrate the code generation workflow.
"""

from typing import Any, Callable, Dict, List

import git_utils
from render_machine import triggers
from render_machine.actions.analyze_specification_ambiguity import AnalyzeSpecificationAmbiguity
from render_machine.actions.commit_conformance_tests_changes import CommitConformanceTestsChanges
from render_machine.actions.commit_implementation_code_changes import CommitImplementationCodeChanges
from render_machine.actions.create_dist import CreateDist
from render_machine.actions.exit_with_error import ExitWithError
from render_machine.actions.finish_functional_requirement import FinishFunctionalRequirement
from render_machine.actions.agent_fix_conformance_test import AgentFixConformanceTest
from render_machine.actions.agent_fix_unit_tests import AgentFixUnitTests
from render_machine.actions.agent_render_functional_requirement import AgentRenderFunctionalRequirement
from render_machine.actions.fix_conformance_test import FixConformanceTest
from render_machine.actions.fix_unit_tests import FixUnitTests
from render_machine.actions.prepare_repositories import PrepareRepositories
from render_machine.actions.prepare_testing_environment import PrepareTestingEnvironment
from render_machine.actions.refactor_code import RefactorCode
from render_machine.actions.render_conformance_tests import RenderConformanceTests
from render_machine.actions.render_functional_requirement import RenderFunctionalRequirement
from render_machine.actions.run_conformance_tests import RunConformanceTests
from render_machine.actions.run_unit_tests import RunUnitTests
from render_machine.actions.summarize_conformance_tests import SummarizeConformanceTests
from render_machine.render_context import RenderContext
from render_machine.states import States


class StateMachineConfig:
    """Configuration class for the render state machine."""

    def get_action_map(self, use_agent: bool = False) -> Dict[str, Any]:
        """Get the mapping of states to their corresponding actions."""
        fix_unit_tests_action = AgentFixUnitTests() if use_agent else FixUnitTests()
        fix_conformance_action = AgentFixConformanceTest() if use_agent else FixConformanceTest()
        render_action = AgentRenderFunctionalRequirement() if use_agent else RenderFunctionalRequirement()
        return {
            States.RENDER_INITIALISED.value: PrepareRepositories(),
            f"{States.IMPLEMENTING_FRID.value}_{States.READY_FOR_FRID_IMPLEMENTATION.value}": render_action,
            f"{States.IMPLEMENTING_FRID.value}_{States.PROCESSING_UNIT_TESTS.value}_{States.UNIT_TESTS_READY.value}": RunUnitTests(),
            f"{States.IMPLEMENTING_FRID.value}_{States.PROCESSING_UNIT_TESTS.value}_{States.UNIT_TESTS_FAILED.value}": fix_unit_tests_action,
            f"{States.IMPLEMENTING_FRID.value}_{States.STEP_COMPLETED.value}": CommitImplementationCodeChanges(
                git_utils.FUNCTIONAL_REQUIREMENT_IMPLEMENTED_COMMIT_MESSAGE
            ),
            f"{States.IMPLEMENTING_FRID.value}_{States.REFACTORING_CODE.value}_{States.READY_FOR_REFACTORING.value}": RefactorCode(),
            f"{States.IMPLEMENTING_FRID.value}_{States.REFACTORING_CODE.value}_{States.PROCESSING_UNIT_TESTS.value}_{States.UNIT_TESTS_READY.value}": RunUnitTests(),
            f"{States.IMPLEMENTING_FRID.value}_{States.REFACTORING_CODE.value}_{States.PROCESSING_UNIT_TESTS.value}_{States.UNIT_TESTS_FAILED.value}": fix_unit_tests_action,
            f"{States.IMPLEMENTING_FRID.value}_{States.REFACTORING_CODE.value}_{States.STEP_COMPLETED.value}": CommitImplementationCodeChanges(
                git_utils.REFACTORED_CODE_COMMIT_MESSAGE
            ),
            f"{States.IMPLEMENTING_FRID.value}_{States.PROCESSING_CONFORMANCE_TESTS.value}_{States.CONFORMANCE_TESTING_INITIALISED.value}": RenderConformanceTests(),
            f"{States.IMPLEMENTING_FRID.value}_{States.PROCESSING_CONFORMANCE_TESTS.value}_{States.CONFORMANCE_TEST_GENERATED.value}": PrepareTestingEnvironment(),
            f"{States.IMPLEMENTING_FRID.value}_{States.PROCESSING_CONFORMANCE_TESTS.value}_{States.CONFORMANCE_TEST_ENV_PREPARED.value}": RunConformanceTests(),
            f"{States.IMPLEMENTING_FRID.value}_{States.PROCESSING_CONFORMANCE_TESTS.value}_{States.CONFORMANCE_TEST_FAILED.value}": fix_conformance_action,
            f"{States.IMPLEMENTING_FRID.value}_{States.PROCESSING_CONFORMANCE_TESTS.value}_{States.POSTPROCESSING_CONFORMANCE_TESTS.value}_{States.CONFORMANCE_TESTS_READY_FOR_SUMMARY.value}": SummarizeConformanceTests(),
            f"{States.IMPLEMENTING_FRID.value}_{States.PROCESSING_CONFORMANCE_TESTS.value}_{States.POSTPROCESSING_CONFORMANCE_TESTS.value}_{States.CONFORMANCE_TESTS_READY_FOR_COMMIT.value}": CommitConformanceTestsChanges(
                git_utils.CONFORMANCE_TESTS_PASSED_COMMIT_MESSAGE,
                git_utils.FUNCTIONAL_REQUIREMENT_FINISHED_COMMIT_MESSAGE,
            ),
            f"{States.IMPLEMENTING_FRID.value}_{States.PROCESSING_CONFORMANCE_TESTS.value}_{States.POSTPROCESSING_CONFORMANCE_TESTS.value}_{States.CONFORMANCE_TESTS_READY_FOR_AMBIGUITY_ANALYSIS.value}": AnalyzeSpecificationAmbiguity(),
            f"{States.IMPLEMENTING_FRID.value}_{States.FRID_FULLY_IMPLEMENTED.value}": FinishFunctionalRequirement(
                git_utils.FUNCTIONAL_REQUIREMENT_FINISHED_COMMIT_MESSAGE
            ),
            f"{States.IMPLEMENTING_FRID.value}_{States.PROCESSING_CONFORMANCE_TESTS.value}_{States.PROCESSING_UNIT_TESTS.value}_{States.UNIT_TESTS_READY.value}": RunUnitTests(),
            f"{States.IMPLEMENTING_FRID.value}_{States.PROCESSING_CONFORMANCE_TESTS.value}_{States.PROCESSING_UNIT_TESTS.value}_{States.UNIT_TESTS_FAILED.value}": fix_unit_tests_action,
            States.RENDER_COMPLETED.value: CreateDist(),
            States.RENDER_FAILED.value: ExitWithError(),
        }

    def get_action_result_triggers_map(self) -> Dict[str, str]:
        """Get the mapping of action outcomes to state machine triggers."""
        return {
            PrepareRepositories.SUCCESSFUL_OUTCOME: triggers.START_RENDER,
            RenderFunctionalRequirement.SUCCESSFUL_OUTCOME: triggers.RENDER_FUNCTIONAL_REQUIREMENT,
            RenderFunctionalRequirement.FUNCTIONAL_REQUIREMENT_TOO_COMPLEX_OUTCOME: triggers.HANDLE_ERROR,
            RenderFunctionalRequirement.ITERATION_LIMIT_EXCEEDED_OUTCOME: triggers.HANDLE_ERROR,
            RunUnitTests.SUCCESSFUL_OUTCOME: triggers.MARK_UNIT_TESTS_PASSED,
            RunUnitTests.FAILED_OUTCOME: triggers.MARK_UNIT_TESTS_FAILED,
            RunUnitTests.UNRECOVERABLE_ERROR_OUTCOME: triggers.HANDLE_ERROR,
            FixUnitTests.SUCCESSFUL_OUTCOME: triggers.MARK_UNIT_TESTS_READY,
            RefactorCode.SUCCESSFUL_OUTCOME: triggers.REFACTOR_CODE,
            RefactorCode.NO_FILES_REFACTORED_OUTCOME: triggers.PROCEED_FRID_PROCESSING,
            RefactorCode.ITERATION_LIMIT_EXCEEDED_OUTCOME: triggers.PROCEED_FRID_PROCESSING,
            CommitImplementationCodeChanges.SUCCESSFUL_OUTCOME: triggers.PROCEED_FRID_PROCESSING,
            FinishFunctionalRequirement.SUCCESSFUL_OUTCOME: triggers.PROCEED_FRID_PROCESSING,
            CreateDist.SUCCESSFUL_OUTCOME: triggers.FINISH_RENDER,
            RenderConformanceTests.SUCCESSFUL_OUTCOME: triggers.MARK_CONFORMANCE_TESTS_READY,
            PrepareTestingEnvironment.SUCCESSFUL_OUTCOME: triggers.MARK_TESTING_ENVIRONMENT_PREPARED,
            PrepareTestingEnvironment.FAILED_OUTCOME: triggers.HANDLE_ERROR,
            RunConformanceTests.SUCCESSFUL_OUTCOME: triggers.MOVE_TO_NEXT_CONFORMANCE_TEST,
            RunConformanceTests.FAILED_OUTCOME: triggers.MARK_CONFORMANCE_TESTS_FAILED,
            RunConformanceTests.UNRECOVERABLE_ERROR_OUTCOME: triggers.HANDLE_ERROR,
            FixConformanceTest.IMPLEMENTATION_CODE_NOT_UPDATED: triggers.MARK_CONFORMANCE_TESTS_READY,
            FixConformanceTest.IMPLEMENTATION_CODE_UPDATED: triggers.MARK_UNIT_TESTS_READY,
            FixConformanceTest.LIMIT_EXCEEDED_OUTCOME: triggers.HANDLE_ERROR,
            FixConformanceTest.REGENERATE_CONFORMANCE_TESTS_OUTCOME: triggers.MARK_REGENERATION_OF_CONFORMANCE_TESTS,
            CommitConformanceTestsChanges.SUCCESSFUL_OUTCOME_IMPLEMENTATION_UPDATED: triggers.MARK_NEXT_CONFORMANCE_TESTS_POSTPROCESSING_STEP,
            CommitConformanceTestsChanges.SUCCESSFUL_OUTCOME_IMPLEMENTATION_NOT_UPDATED: triggers.PROCEED_FRID_PROCESSING,
            SummarizeConformanceTests.SUCCESSFUL_OUTCOME: triggers.MARK_NEXT_CONFORMANCE_TESTS_POSTPROCESSING_STEP,
            AnalyzeSpecificationAmbiguity.SUCCESSFUL_OUTCOME: triggers.PROCEED_FRID_PROCESSING,
        }

    def get_processing_unit_tests_states(
        self, render_context: RenderContext, on_limit_exceeded: Callable
    ) -> Dict[str, Any]:
        return {
            "name": States.PROCESSING_UNIT_TESTS.value,
            "initial": States.UNIT_TESTS_READY.value,
            "on_enter": render_context.start_unittests_processing,
            "on_exit": render_context.finish_unittests_processing,
            "children": [
                States.UNIT_TESTS_READY.value,
                {
                    "name": States.UNIT_TESTS_FAILED.value,
                    "on_enter": lambda: render_context.start_fixing_unit_tests(on_limit_exceeded),
                },
            ],
        }

    def get_postprocessing_conformance_tests_states(self) -> Dict[str, Any]:
        return {
            "name": States.POSTPROCESSING_CONFORMANCE_TESTS.value,
            "initial": States.CONFORMANCE_TESTS_READY_FOR_SUMMARY.value,
            "children": [
                States.CONFORMANCE_TESTS_READY_FOR_SUMMARY.value,
                States.CONFORMANCE_TESTS_READY_FOR_COMMIT.value,
                States.CONFORMANCE_TESTS_READY_FOR_AMBIGUITY_ANALYSIS.value,
            ],
        }

    def get_processing_conformance_tests_states(self, render_context: RenderContext) -> Dict[str, Any]:
        return {
            "name": States.PROCESSING_CONFORMANCE_TESTS.value,
            "initial": States.CONFORMANCE_TESTING_INITIALISED.value,
            "on_enter": render_context.start_conformance_tests_processing,
            "on_exit": render_context.finish_conformance_tests_processing,
            "children": [
                {
                    "name": States.CONFORMANCE_TESTING_INITIALISED.value,
                    "on_enter": render_context.start_conformance_tests_for_frid,
                },
                States.CONFORMANCE_TEST_GENERATED.value,
                States.CONFORMANCE_TEST_ENV_PREPARED.value,
                States.CONFORMANCE_TEST_FAILED.value,
                self.get_processing_unit_tests_states(
                    render_context, render_context._on_unit_test_limit_exceeded_in_conformance_tests
                ),
                self.get_postprocessing_conformance_tests_states(),
            ],
        }

    def get_states(self, render_context: RenderContext) -> List[Any]:
        """Get the complete state machine state configuration.

        Args:
            render_context: The render context object containing callback methods.

        Returns:
            List of state definitions for the hierarchical state machine.
        """
        refactoring_code_states = {
            "name": States.REFACTORING_CODE.value,
            "initial": States.READY_FOR_REFACTORING.value,
            "children": [
                States.READY_FOR_REFACTORING.value,
                self.get_processing_unit_tests_states(
                    render_context, render_context._on_unit_test_limit_exceeded_in_refactoring
                ),
                States.STEP_COMPLETED.value,
            ],
        }

        return [
            States.RENDER_INITIALISED.value,
            {
                "name": States.IMPLEMENTING_FRID.value,
                "initial": States.READY_FOR_FRID_IMPLEMENTATION.value,
                "on_enter": render_context.start_implementing_frid,
                "on_exit": render_context.finish_implementing_frid,
                "children": [
                    {"name": States.STEP_COMPLETED.value},
                    States.READY_FOR_FRID_IMPLEMENTATION.value,
                    self.get_processing_unit_tests_states(
                        render_context, render_context._on_unit_test_limit_exceeded_in_implementation
                    ),
                    refactoring_code_states,
                    self.get_processing_conformance_tests_states(render_context),
                    States.FRID_FULLY_IMPLEMENTED.value,
                ],
            },
            {"name": States.RENDER_COMPLETED.value, "on_enter": render_context.start_render_completed},
            {"name": States.RENDER_FAILED.value, "on_enter": render_context.start_render_failed},
        ]

    def get_transitions(self, render_context: RenderContext) -> List[Dict[str, Any]]:
        """Get the complete state machine transition configuration.

        Args:
            render_context: The render context object containing condition methods.

        Returns:
            List of transition definitions for the hierarchical state machine.
        """
        return [
            {
                "source": States.RENDER_INITIALISED.value,
                "trigger": triggers.START_RENDER,
                "dest": States.IMPLEMENTING_FRID.value,
            },
            {
                "source": f"{States.IMPLEMENTING_FRID.value}_{States.READY_FOR_FRID_IMPLEMENTATION.value}",
                "trigger": triggers.RENDER_FUNCTIONAL_REQUIREMENT,
                "dest": f"{States.IMPLEMENTING_FRID.value}_{States.PROCESSING_UNIT_TESTS.value}",
                "conditions": render_context.should_run_unit_tests,
            },
            {
                "source": f"{States.IMPLEMENTING_FRID.value}_{States.READY_FOR_FRID_IMPLEMENTATION.value}",
                "trigger": triggers.RENDER_FUNCTIONAL_REQUIREMENT,
                "dest": f"{States.IMPLEMENTING_FRID.value}_{States.STEP_COMPLETED.value}",
                "unless": render_context.should_run_unit_tests,
            },
            {
                "source": "*",
                "trigger": triggers.HANDLE_ERROR,
                "dest": States.RENDER_FAILED.value,
            },
            {
                "source": f"{States.IMPLEMENTING_FRID.value}_{States.PROCESSING_UNIT_TESTS.value}_{States.UNIT_TESTS_READY.value}",
                "trigger": triggers.MARK_UNIT_TESTS_FAILED,
                "dest": f"{States.IMPLEMENTING_FRID.value}_{States.PROCESSING_UNIT_TESTS.value}_{States.UNIT_TESTS_FAILED.value}",
            },
            {
                "source": f"{States.IMPLEMENTING_FRID.value}_{States.PROCESSING_UNIT_TESTS.value}_{States.UNIT_TESTS_READY.value}",
                "trigger": triggers.MARK_UNIT_TESTS_PASSED,
                "dest": f"{States.IMPLEMENTING_FRID.value}_{States.STEP_COMPLETED.value}",
            },
            {
                "source": f"{States.IMPLEMENTING_FRID.value}_{States.STEP_COMPLETED.value}",
                "trigger": triggers.PROCEED_FRID_PROCESSING,
                "dest": f"{States.IMPLEMENTING_FRID.value}_{States.REFACTORING_CODE.value}",
            },
            {
                "source": f"{States.IMPLEMENTING_FRID.value}_{States.PROCESSING_UNIT_TESTS.value}_{States.UNIT_TESTS_FAILED.value}",
                "trigger": triggers.MARK_UNIT_TESTS_READY,
                "dest": f"{States.IMPLEMENTING_FRID.value}_{States.PROCESSING_UNIT_TESTS.value}_{States.UNIT_TESTS_READY.value}",
            },
            {
                "source": f"{States.IMPLEMENTING_FRID.value}_{States.PROCESSING_UNIT_TESTS.value}_{States.UNIT_TESTS_FAILED.value}",
                "trigger": triggers.RESTART_FRID_PROCESSING,
                "dest": f"{States.IMPLEMENTING_FRID.value}_{States.READY_FOR_FRID_IMPLEMENTATION.value}",
            },
            {
                "source": f"{States.IMPLEMENTING_FRID.value}_{States.REFACTORING_CODE.value}_{States.READY_FOR_REFACTORING.value}",
                "trigger": triggers.REFACTOR_CODE,
                "dest": f"{States.IMPLEMENTING_FRID.value}_{States.REFACTORING_CODE.value}_{States.PROCESSING_UNIT_TESTS.value}",
                "conditions": render_context.should_run_unit_tests,
            },
            {
                "source": f"{States.IMPLEMENTING_FRID.value}_{States.REFACTORING_CODE.value}_{States.READY_FOR_REFACTORING.value}",
                "trigger": triggers.REFACTOR_CODE,
                "dest": f"{States.IMPLEMENTING_FRID.value}_{States.REFACTORING_CODE.value}_{States.STEP_COMPLETED.value}",
                "unless": render_context.should_run_unit_tests,
            },
            {
                "source": f"{States.IMPLEMENTING_FRID.value}_{States.REFACTORING_CODE.value}_{States.READY_FOR_REFACTORING.value}",
                "trigger": triggers.PROCEED_FRID_PROCESSING,
                "dest": f"{States.IMPLEMENTING_FRID.value}_{States.PROCESSING_CONFORMANCE_TESTS.value}",
                "conditions": render_context.should_run_conformance_tests,
            },
            {
                "source": f"{States.IMPLEMENTING_FRID.value}_{States.REFACTORING_CODE.value}_{States.READY_FOR_REFACTORING.value}",
                "trigger": triggers.PROCEED_FRID_PROCESSING,
                "dest": f"{States.IMPLEMENTING_FRID.value}_{States.FRID_FULLY_IMPLEMENTED.value}",
                "unless": render_context.should_run_conformance_tests,
            },
            {
                "source": f"{States.IMPLEMENTING_FRID.value}_{States.PROCESSING_CONFORMANCE_TESTS.value}",
                "trigger": triggers.MARK_ALL_CONFORMANCE_TESTS_PASSED,
                "dest": f"{States.IMPLEMENTING_FRID.value}_{States.PROCESSING_CONFORMANCE_TESTS.value}_{States.POSTPROCESSING_CONFORMANCE_TESTS.value}",
            },
            {
                "source": f"{States.IMPLEMENTING_FRID.value}_{States.PROCESSING_CONFORMANCE_TESTS.value}_{States.POSTPROCESSING_CONFORMANCE_TESTS.value}_{States.CONFORMANCE_TESTS_READY_FOR_SUMMARY.value}",
                "trigger": triggers.MARK_NEXT_CONFORMANCE_TESTS_POSTPROCESSING_STEP,
                "dest": f"{States.IMPLEMENTING_FRID.value}_{States.PROCESSING_CONFORMANCE_TESTS.value}_{States.POSTPROCESSING_CONFORMANCE_TESTS.value}_{States.CONFORMANCE_TESTS_READY_FOR_COMMIT.value}",
            },
            {
                "source": f"{States.IMPLEMENTING_FRID.value}_{States.PROCESSING_CONFORMANCE_TESTS.value}_{States.POSTPROCESSING_CONFORMANCE_TESTS.value}_{States.CONFORMANCE_TESTS_READY_FOR_COMMIT.value}",
                "trigger": triggers.MARK_NEXT_CONFORMANCE_TESTS_POSTPROCESSING_STEP,
                "dest": f"{States.IMPLEMENTING_FRID.value}_{States.PROCESSING_CONFORMANCE_TESTS.value}_{States.POSTPROCESSING_CONFORMANCE_TESTS.value}_{States.CONFORMANCE_TESTS_READY_FOR_AMBIGUITY_ANALYSIS.value}",
            },
            {
                "source": f"{States.IMPLEMENTING_FRID.value}_{States.PROCESSING_CONFORMANCE_TESTS.value}_{States.POSTPROCESSING_CONFORMANCE_TESTS.value}_{States.CONFORMANCE_TESTS_READY_FOR_COMMIT.value}",
                "trigger": triggers.PROCEED_FRID_PROCESSING,
                "dest": f"{States.IMPLEMENTING_FRID.value}_{States.FRID_FULLY_IMPLEMENTED.value}",
            },
            {
                "source": f"{States.IMPLEMENTING_FRID.value}_{States.PROCESSING_CONFORMANCE_TESTS.value}_{States.POSTPROCESSING_CONFORMANCE_TESTS.value}_{States.CONFORMANCE_TESTS_READY_FOR_AMBIGUITY_ANALYSIS.value}",
                "trigger": triggers.PROCEED_FRID_PROCESSING,
                "dest": f"{States.IMPLEMENTING_FRID.value}_{States.FRID_FULLY_IMPLEMENTED.value}",
            },
            {
                "source": f"{States.IMPLEMENTING_FRID.value}_{States.FRID_FULLY_IMPLEMENTED.value}",
                "trigger": triggers.PROCEED_FRID_PROCESSING,
                "dest": f"{States.IMPLEMENTING_FRID.value}",
                "conditions": render_context.has_next_frid,
            },
            {
                "source": f"{States.IMPLEMENTING_FRID.value}_{States.FRID_FULLY_IMPLEMENTED.value}",
                "trigger": triggers.PROCEED_FRID_PROCESSING,
                "dest": States.RENDER_COMPLETED.value,
                "unless": render_context.has_next_frid,
            },
            {
                "source": f"{States.IMPLEMENTING_FRID.value}_{States.REFACTORING_CODE.value}_{States.PROCESSING_UNIT_TESTS.value}_{States.UNIT_TESTS_READY.value}",
                "trigger": triggers.MARK_UNIT_TESTS_FAILED,
                "dest": f"{States.IMPLEMENTING_FRID.value}_{States.REFACTORING_CODE.value}_{States.PROCESSING_UNIT_TESTS.value}_{States.UNIT_TESTS_FAILED.value}",
            },
            {
                "source": f"{States.IMPLEMENTING_FRID.value}_{States.REFACTORING_CODE.value}_{States.PROCESSING_UNIT_TESTS.value}_{States.UNIT_TESTS_READY.value}",
                "trigger": triggers.MARK_UNIT_TESTS_PASSED,
                "dest": f"{States.IMPLEMENTING_FRID.value}_{States.REFACTORING_CODE.value}_{States.STEP_COMPLETED.value}",
            },
            {
                "source": f"{States.IMPLEMENTING_FRID.value}_{States.REFACTORING_CODE.value}_{States.PROCESSING_UNIT_TESTS.value}_{States.UNIT_TESTS_FAILED.value}",
                "trigger": triggers.MARK_UNIT_TESTS_READY,
                "dest": f"{States.IMPLEMENTING_FRID.value}_{States.REFACTORING_CODE.value}_{States.PROCESSING_UNIT_TESTS.value}_{States.UNIT_TESTS_READY.value}",
            },
            {
                "source": f"{States.IMPLEMENTING_FRID.value}_{States.REFACTORING_CODE.value}_{States.PROCESSING_UNIT_TESTS.value}_{States.UNIT_TESTS_FAILED.value}",
                "trigger": triggers.START_NEW_REFACTORING_ITERATION,
                "dest": f"{States.IMPLEMENTING_FRID.value}_{States.REFACTORING_CODE.value}_{States.READY_FOR_REFACTORING.value}",
            },
            {
                "source": f"{States.IMPLEMENTING_FRID.value}_{States.REFACTORING_CODE.value}_{States.STEP_COMPLETED.value}",
                "trigger": triggers.PROCEED_FRID_PROCESSING,
                "dest": f"{States.IMPLEMENTING_FRID.value}_{States.REFACTORING_CODE.value}_{States.READY_FOR_REFACTORING.value}",
            },
            {
                "source": f"{States.IMPLEMENTING_FRID.value}_{States.PROCESSING_CONFORMANCE_TESTS.value}_{States.CONFORMANCE_TESTING_INITIALISED.value}",
                "trigger": triggers.MARK_CONFORMANCE_TESTS_READY,
                "dest": f"{States.IMPLEMENTING_FRID.value}_{States.PROCESSING_CONFORMANCE_TESTS.value}_{States.CONFORMANCE_TEST_GENERATED.value}",
            },
            {
                "source": f"{States.IMPLEMENTING_FRID.value}_{States.PROCESSING_CONFORMANCE_TESTS.value}_{States.CONFORMANCE_TEST_GENERATED.value}",
                "trigger": triggers.MARK_TESTING_ENVIRONMENT_PREPARED,
                "dest": f"{States.IMPLEMENTING_FRID.value}_{States.PROCESSING_CONFORMANCE_TESTS.value}_{States.CONFORMANCE_TEST_ENV_PREPARED.value}",
            },
            {
                "source": f"{States.IMPLEMENTING_FRID.value}_{States.PROCESSING_CONFORMANCE_TESTS.value}_{States.CONFORMANCE_TEST_ENV_PREPARED.value}",
                "trigger": triggers.MARK_CONFORMANCE_TESTS_FAILED,
                "dest": f"{States.IMPLEMENTING_FRID.value}_{States.PROCESSING_CONFORMANCE_TESTS.value}_{States.CONFORMANCE_TEST_FAILED.value}",
            },
            {
                "source": f"{States.IMPLEMENTING_FRID.value}_{States.PROCESSING_CONFORMANCE_TESTS.value}_{States.CONFORMANCE_TEST_FAILED.value}",
                "trigger": triggers.MARK_REGENERATION_OF_CONFORMANCE_TESTS,
                "dest": f"{States.IMPLEMENTING_FRID.value}_{States.PROCESSING_CONFORMANCE_TESTS.value}_{States.CONFORMANCE_TESTING_INITIALISED.value}",
            },
            {
                "source": f"{States.IMPLEMENTING_FRID.value}_{States.PROCESSING_CONFORMANCE_TESTS.value}_{States.CONFORMANCE_TEST_ENV_PREPARED.value}",
                "trigger": triggers.MOVE_TO_NEXT_CONFORMANCE_TEST,
                "dest": f"{States.IMPLEMENTING_FRID.value}_{States.PROCESSING_CONFORMANCE_TESTS.value}_{States.CONFORMANCE_TESTING_INITIALISED.value}",
            },
            {
                "source": f"{States.IMPLEMENTING_FRID.value}_{States.PROCESSING_CONFORMANCE_TESTS.value}_{States.CONFORMANCE_TEST_FAILED.value}",
                "trigger": triggers.MARK_CONFORMANCE_TESTS_READY,
                "dest": f"{States.IMPLEMENTING_FRID.value}_{States.PROCESSING_CONFORMANCE_TESTS.value}_{States.CONFORMANCE_TEST_ENV_PREPARED.value}",
            },
            {
                "source": f"{States.IMPLEMENTING_FRID.value}_{States.PROCESSING_CONFORMANCE_TESTS.value}_{States.CONFORMANCE_TEST_FAILED.value}",
                "trigger": triggers.MARK_UNIT_TESTS_READY,
                "dest": f"{States.IMPLEMENTING_FRID.value}_{States.PROCESSING_CONFORMANCE_TESTS.value}_{States.PROCESSING_UNIT_TESTS.value}",
                "conditions": render_context.should_run_unit_tests,
            },
            {
                "source": f"{States.IMPLEMENTING_FRID.value}_{States.PROCESSING_CONFORMANCE_TESTS.value}_{States.CONFORMANCE_TEST_FAILED.value}",
                "trigger": triggers.MARK_UNIT_TESTS_READY,
                "dest": f"{States.IMPLEMENTING_FRID.value}_{States.PROCESSING_CONFORMANCE_TESTS.value}_{States.CONFORMANCE_TEST_ENV_PREPARED.value}",
                "unless": render_context.should_run_unit_tests,
            },
            {
                "source": f"{States.IMPLEMENTING_FRID.value}_{States.PROCESSING_CONFORMANCE_TESTS.value}_{States.PROCESSING_UNIT_TESTS.value}_{States.UNIT_TESTS_READY.value}",
                "trigger": triggers.MARK_UNIT_TESTS_PASSED,
                "dest": f"{States.IMPLEMENTING_FRID.value}_{States.PROCESSING_CONFORMANCE_TESTS.value}_{States.CONFORMANCE_TEST_GENERATED.value}",
            },
            {
                "source": f"{States.IMPLEMENTING_FRID.value}_{States.PROCESSING_CONFORMANCE_TESTS.value}_{States.PROCESSING_UNIT_TESTS.value}_{States.UNIT_TESTS_READY.value}",
                "trigger": triggers.MARK_UNIT_TESTS_FAILED,
                "dest": f"{States.IMPLEMENTING_FRID.value}_{States.PROCESSING_CONFORMANCE_TESTS.value}_{States.PROCESSING_UNIT_TESTS.value}_{States.UNIT_TESTS_FAILED.value}",
            },
            {
                "source": f"{States.IMPLEMENTING_FRID.value}_{States.PROCESSING_CONFORMANCE_TESTS.value}_{States.PROCESSING_UNIT_TESTS.value}_{States.UNIT_TESTS_FAILED.value}",
                "trigger": triggers.MARK_UNIT_TESTS_READY,
                "dest": f"{States.IMPLEMENTING_FRID.value}_{States.PROCESSING_CONFORMANCE_TESTS.value}_{States.PROCESSING_UNIT_TESTS.value}_{States.UNIT_TESTS_READY.value}",
            },
            {
                "source": f"{States.IMPLEMENTING_FRID.value}_{States.PROCESSING_CONFORMANCE_TESTS.value}_{States.PROCESSING_UNIT_TESTS.value}_{States.UNIT_TESTS_FAILED.value}",
                "trigger": triggers.RESTART_FRID_PROCESSING,
                "dest": f"{States.IMPLEMENTING_FRID.value}_{States.READY_FOR_FRID_IMPLEMENTATION.value}",
            },
        ]
