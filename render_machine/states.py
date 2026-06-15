"""
State name constants for the hierarchical state machine.

This module defines the state name constants used by the state machine configuration.
Note: State names must be in camelCase due to hierarchical graph machine requirements.
"""

from enum import Enum


class States(Enum):
    """State name constants for the hierarchical state machine.

    Note: State names must be in camelCase due to hierarchical graph machine requirements.
    TODO: Consider standardizing on present vs past tense for state names.
    """

    # Root level states
    RENDER_INITIALISED = "renderInitialised"
    IMPLEMENTING_FRID = "implementingFrid"
    RENDER_COMPLETED = "renderCompleted"
    RENDER_FAILED = "renderFailed"
    STEP_COMPLETED = "stepCompleted"

    # FRID implementation states
    READY_FOR_FRID_IMPLEMENTATION = "readyForFridImplementation"
    FRID_FULLY_IMPLEMENTED = "fridFullyImplemented"

    # Unit test processing states
    PROCESSING_UNIT_TESTS = "processingUnitTests"
    UNIT_TESTS_READY = "unittestsReady"
    UNIT_TESTS_FAILED = "unittestsFailed"

    # Code refactoring states
    REFACTORING_CODE = "refactoringCode"
    READY_FOR_REFACTORING = "readyForRefactoring"

    # Conformance test processing states
    PROCESSING_CONFORMANCE_TESTS = "processingConformanceTests"
    CONFORMANCE_TESTING_INITIALISED = "conformanceTestingInitialised"
    CONFORMANCE_TEST_GENERATED = "conformanceTestGenerated"
    CONFORMANCE_TEST_ENV_PREPARED = "conformanceTestEnvironmentPrepared"
    CONFORMANCE_TEST_FAILED = "conformanceTestFailed"
    # The fix agent has applied a fix; the environment is (re)prepared and tests
    # re-run from here. Review happens only afterwards, and only if tests pass.
    CONFORMANCE_FIX_APPLIED = "conformanceFixApplied"
    CONFORMANCE_FIX_READY_FOR_REVIEW = "conformanceFixReadyForReview"

    # Postprocessing conformance tests states
    POSTPROCESSING_CONFORMANCE_TESTS = "postprocessingConformanceTests"
    CONFORMANCE_TESTS_READY_FOR_SUMMARY = "conformanceTestsReadyForSummary"
    CONFORMANCE_TESTS_READY_FOR_COMMIT = "conformanceTestsReadyForCommit"
    CONFORMANCE_TESTS_READY_FOR_AMBIGUITY_ANALYSIS = "conformanceTestsReadyForAmbiguityAnalysis"

    def __str__(self):
        return self.value
