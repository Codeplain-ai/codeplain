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

    # Module conformance test processing states. Conformance tests are scoped to the whole module, so
    # this is a root-level phase that runs once, after every functionality has been implemented -
    # not a child of IMPLEMENTING_FRID the way unit testing and refactoring are.
    PROCESSING_MODULE_CONFORMANCE_TESTS = "processingModuleConformanceTests"
    MODULE_CONFORMANCE_TESTING_INITIALISED = "moduleConformanceTestingInitialised"
    MODULE_CONFORMANCE_TESTS_PLANNED = "moduleConformanceTestsPlanned"
    MODULE_CONFORMANCE_TESTS_GENERATED = "moduleConformanceTestsGenerated"
    MODULE_CONFORMANCE_TESTS_ENV_PREPARED = "moduleConformanceTestsEnvironmentPrepared"
    MODULE_CONFORMANCE_TESTS_FAILED = "moduleConformanceTestsFailed"

    # Postprocessing module conformance tests states
    POSTPROCESSING_MODULE_CONFORMANCE_TESTS = "postprocessingModuleConformanceTests"
    MODULE_CONFORMANCE_TESTS_READY_FOR_SUMMARY = "moduleConformanceTestsReadyForSummary"
    MODULE_CONFORMANCE_TESTS_READY_FOR_COMMIT = "moduleConformanceTestsReadyForCommit"
    MODULE_CONFORMANCE_TESTS_READY_FOR_AMBIGUITY_ANALYSIS = "moduleConformanceTestsReadyForAmbiguityAnalysis"
    MODULE_FULLY_IMPLEMENTED = "moduleFullyImplemented"

    def __str__(self):
        return self.value
