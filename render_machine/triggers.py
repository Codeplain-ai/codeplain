"""
State machine trigger constants for the render machine.

These triggers are used to transition between states in the hierarchical state machine
that controls the code rendering process.
"""

# Trigger constants for state machine transitions
START_RENDER = "start_render"
RENDER_FUNCTIONAL_REQUIREMENT = "render_functional_requirement"
PROCEED_FRID_PROCESSING = "proceed_frid_processing"
MARK_UNIT_TESTS_FAILED = "mark_unit_tests_failed"
MARK_UNIT_TESTS_PASSED = "mark_unit_tests_passed"
MARK_UNIT_TESTS_READY = "mark_unit_tests_ready"
FINISH_RENDER = "finish_render"
HANDLE_ERROR = "handle_error"
REFACTOR_CODE = "refactor_code"
RESTART_FRID_PROCESSING = "restart_frid_processing"
START_NEW_REFACTORING_ITERATION = "start_new_refactoring_iteration"
MARK_CONFORMANCE_TESTS_READY = "mark_conformance_tests_ready"
MARK_TESTING_ENVIRONMENT_PREPARED = "mark_testing_environment_prepared"
MARK_CONFORMANCE_TESTS_FAILED = "mark_conformance_tests_failed"
MOVE_TO_NEXT_CONFORMANCE_TEST = "move_to_next_conformance_test"
MARK_ALL_CONFORMANCE_TESTS_PASSED = "mark_all_conformance_tests_passed"
MARK_REGENERATION_OF_CONFORMANCE_TESTS = "mark_regeneration_of_conformance_tests"
MARK_NEXT_CONFORMANCE_TESTS_POSTPROCESSING_STEP = "mark_next_conformance_tests_postprocessing_step"

# Module-scoped conformance testing. The triggers below drive the root-level phase that runs once per
# module, after every functionality has been implemented.
START_MODULE_CONFORMANCE_TESTING = "start_module_conformance_testing"
MARK_MODULE_CONFORMANCE_TESTS_PLANNED = "mark_module_conformance_tests_planned"
MARK_MODULE_CONFORMANCE_TESTS_READY = "mark_module_conformance_tests_ready"
MARK_MODULE_TESTING_ENVIRONMENT_PREPARED = "mark_module_testing_environment_prepared"
MARK_MODULE_CONFORMANCE_TESTS_FAILED = "mark_module_conformance_tests_failed"
MOVE_TO_NEXT_MODULE_CONFORMANCE_SUITE = "move_to_next_module_conformance_suite"
MARK_ALL_MODULE_CONFORMANCE_TESTS_PASSED = "mark_all_module_conformance_tests_passed"
MARK_NEXT_MODULE_CONFORMANCE_TESTS_POSTPROCESSING_STEP = "mark_next_module_conformance_tests_postprocessing_step"
PROCEED_MODULE_CONFORMANCE_TESTING = "proceed_module_conformance_testing"
