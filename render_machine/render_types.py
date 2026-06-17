import os
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Optional

import plain_spec


class TestExecutionPhase(Enum):
    """Explicit phases of conformance test execution."""

    # Initial testing of current FRID
    TESTING_CURRENT_FRID = auto()

    # Running regression tests on earlier FRIDs
    RUNNING_REGRESSION = auto()

    # Re-running a specific test that failed after code change
    RETRYING_AFTER_CODE_CHANGE = auto()

    # All tests passed
    COMPLETED = auto()


class AcceptanceTestPhase(Enum):
    """Phases for incremental acceptance test execution."""

    # No acceptance tests have been run yet
    NOT_STARTED = auto()

    # Currently running acceptance tests incrementally
    IN_PROGRESS = auto()

    # All acceptance tests completed
    COMPLETED = auto()

    # No acceptance tests defined for this FRID
    NOT_APPLICABLE = auto()


@dataclass
class FridContext:
    frid: str
    specifications: dict
    functional_requirement_text: str
    linked_resources: dict
    functional_requirement_render_attempts: int = 0
    changed_files: set[str] = field(default_factory=set)
    refactoring_iteration: int = 0


@dataclass
class UnitTestsRunningContext:
    fix_attempts: int
    changed_files: set[str] = field(default_factory=set)


class ConformanceTestsRunningContext:
    def __init__(
        self,
        current_testing_module_name: str,
        current_testing_frid: Optional[str],
        fix_attempts: int,
        conformance_tests_json: dict,
        conformance_tests_render_attempts: int,
        current_testing_frid_specifications: Optional[dict[str, list]],
        should_prepare_testing_environment: bool,
        conflicting_requirement_count: int = 0,
        conflicting_module_name: Optional[str] = None,
        conflicting_frid: Optional[str] = None,
        frid_being_implemented: Optional[str] = None,
    ):
        self.current_testing_module_name = current_testing_module_name
        self.current_testing_frid = current_testing_frid
        self.fix_attempts = fix_attempts
        self._conformance_tests_json = {current_testing_module_name: conformance_tests_json}
        self.conformance_tests_render_attempts = conformance_tests_render_attempts
        self.current_testing_frid_specifications = current_testing_frid_specifications
        self.should_prepare_testing_environment = should_prepare_testing_environment
        self.conflicting_requirement_count = conflicting_requirement_count
        self.conflicting_module_name = conflicting_module_name
        self.conflicting_frid = conflicting_frid

        self.execution_phase: TestExecutionPhase = TestExecutionPhase.TESTING_CURRENT_FRID
        self.acceptance_test_phase: AcceptanceTestPhase = AcceptanceTestPhase.NOT_STARTED
        self.acceptance_tests_completed: int = 0
        self.frid_being_implemented: Optional[str] = frid_being_implemented
        self.test_that_triggered_code_change: Optional[tuple[str, str]] = None
        self.code_changed_during_regression: bool = False

        self.regenerating_conformance_tests: bool = False

        # Tracks original file contents before the fix agent first modifies them,
        # accumulated across all fix attempts since the last review.
        # Key: absolute file path, Value: original content (or None if file didn't exist).
        # Used by ReviewConformanceFixAction to compute the cumulative fix diff and to
        # revert changes when the reviewer rejects a fix. Cleared only by the reviewer.
        self.file_change_tracker: dict[str, Optional[str]] = {}

        self.current_testing_frid_high_level_implementation_plan: Optional[str] = None
        self.previous_conformance_tests_issue_old: Optional[str] = None
        self.previous_conformance_tests_issue_frid: Optional[str] = None
        self.previous_conformance_tests_issue_module: Optional[str] = None
        self.code_diff_files: Optional[dict[str, str]] = None
        self.fix_agent_session_id: Optional[str] = None  # Persistent session for fix agent
        # The tool-call id of the most recent submit_fix call that has not yet been
        # answered. The review/test feedback is delivered back as this call's tool
        # result (rather than a new user message) so the agent's tool loop — and thus
        # Gemini's prompt cache — is preserved across fix attempts.
        self.fix_agent_pending_tool_call_id: Optional[str] = None
        # Handoff notes authored by fix agents that ran out of turns without resolving
        # the failure. Each entry is a fresh agent's self-written summary for its
        # successor (what was tried, why, why it failed, current code state, next
        # steps). Carried into the next fresh session so a post-rotation agent does not
        # start blind. Replaces the previous per-attempt fix_history log.
        self.fix_handoffs: list[str] = []
        self.last_fix_summary: Optional[dict] = None  # Structured output from last submit_fix call
        # True once a fix has been applied and is awaiting integrity review. The
        # review only runs after the applied fix makes the conformance tests pass,
        # so this gates the "tests passed -> review" transition.
        self.pending_fix_review: bool = False
        # Inputs the reviewer needs (specifications, acceptance tests, conformance
        # test folder), stashed by the fix agent. The reviewer no longer runs
        # directly after the fix agent (tests run in between), so it cannot rely on
        # the previous action's payload and reads these from the context instead.
        self.fix_review_context: Optional[dict] = None

    def get_conformance_tests_json(self, module_name: str) -> dict:
        return self._conformance_tests_json[module_name]

    def conformance_tests_json_has_module_populated(self, module_name: str) -> bool:
        return module_name in self._conformance_tests_json and len(self._conformance_tests_json[module_name]) > 0

    def set_conformance_tests_json(self, module_name: str, conformance_tests_json: dict):
        self._conformance_tests_json[module_name] = conformance_tests_json

    def get_current_conformance_test_folder_name(self) -> str:
        return self.get_conformance_tests_json(self.current_testing_module_name)[self.current_testing_frid][
            "folder_name"
        ]

    def current_conformance_tests_exist(self) -> bool:
        return (
            self.get_conformance_tests_json(self.current_testing_module_name).get(self.current_testing_frid) is not None
        )

    def get_current_acceptance_tests(self) -> Optional[list[str]]:
        if (
            plain_spec.ACCEPTANCE_TESTS
            in self.get_conformance_tests_json(self.current_testing_module_name)[self.current_testing_frid]
        ):
            return self.get_conformance_tests_json(self.current_testing_module_name)[self.current_testing_frid][
                plain_spec.ACCEPTANCE_TESTS
            ]

        return []

    def get_current_acceptance_test(self) -> Optional[str]:
        """Get the current acceptance test text (raw, unformatted)."""
        if plain_spec.ACCEPTANCE_TESTS not in self.current_testing_frid_specifications:
            return None
        acceptance_tests = self.current_testing_frid_specifications[plain_spec.ACCEPTANCE_TESTS]
        if not acceptance_tests or self.acceptance_tests_completed == 0:
            return None
        return acceptance_tests[self.acceptance_tests_completed - 1]

    def set_conformance_tests_summary(self, summary: list[dict]):
        self.get_conformance_tests_json(self.current_testing_module_name)[self.current_testing_frid][
            "test_summary"
        ] = summary

    def reset_file_change_tracker(self):
        """Clear the file change tracker.

        Reset at exactly two points, which together bracket a fix loop:
          - AgentRenderConformanceTests, after the tests are rendered. This sets the
            baseline so the reviewer's diff captures only the fix loop's changes, not
            the test-rendering writes.
          - ReviewConformanceFixAction, when a reviewed fix cycle ends (on approval;
            rejection clears it via revert_tracked_changes instead).

        It is NOT reset per fix attempt, so within one fix loop the tracker accumulates
        the original-file snapshots across all attempts since the baseline — giving the
        reviewer the cumulative diff against the rendered-tests baseline rather than
        only the last attempt's changes.
        """
        self.file_change_tracker = {}

    def track_file_before_modification(self, absolute_path: str):
        """Record original file content before first modification. No-op if already tracked."""
        if absolute_path in self.file_change_tracker:
            return
        if os.path.exists(absolute_path):
            with open(absolute_path, "r", encoding="utf-8") as f:
                self.file_change_tracker[absolute_path] = f.read()
        else:
            self.file_change_tracker[absolute_path] = None

    def revert_tracked_changes(self):
        """Revert all tracked files to their original state."""
        for absolute_path, original_content in self.file_change_tracker.items():
            if original_content is None:
                # File didn't exist before — delete it
                if os.path.exists(absolute_path):
                    os.remove(absolute_path)
            else:
                os.makedirs(os.path.dirname(absolute_path), exist_ok=True)
                with open(absolute_path, "w", encoding="utf-8") as f:
                    f.write(original_content)
        self.file_change_tracker = {}


@dataclass
class ScriptExecutionHistory:
    latest_unit_test_output_path: Optional[str] = None
    latest_conformance_test_output_path: Optional[str] = None
    latest_testing_environment_output_path: Optional[str] = None
    should_update_script_outputs: bool = False


@dataclass
class RenderError:
    """Standardized error format for all render failures."""

    message: str
    error_type: str | None = None
    details: dict | None = None

    @classmethod
    def encode(cls, message: str, error_type: str | None = None, **details) -> "RenderError":
        """Factory method to create a standardized error."""
        return cls(message=message, error_type=error_type, details=details or None)

    def to_payload(self) -> dict:
        """Convert to action payload format."""
        return {"error": {"message": self.message, "type": self.error_type, "details": self.details}}

    def format_for_display(self) -> str:
        """Format complete error with details for user display."""
        lines = [self.message]

        if self.details:
            lines.append("\nDetails:")
            for detail_name, detail_value in self.details.items():
                if detail_name == "issue":
                    detail_value_indented = "\n".join("  " + line for line in detail_value.splitlines())
                    lines.append(detail_value_indented)
                else:
                    lines.append(f"  {detail_name.capitalize()}: {detail_value}")

        return "\n".join(lines)

    @classmethod
    def get_display_message(cls, payload: Any, fallback_message: str | None = None) -> str:
        """Extract and format error message from payload with fallback.

        Priority:
        1. Extract from action payload
        2. Use fallback message if provided
        3. Use default fallback

        Args:
            payload: Action payload to extract error from
            fallback_message: Optional fallback message (e.g., from context)

        Returns:
            Formatted error message string
        """
        # Priority 1: Extract from action payload
        render_error = cls.from_payload(payload)
        if render_error and render_error.message:
            return render_error.format_for_display()

        # Priority 2: Use provided fallback
        if fallback_message:
            return fallback_message

        # Priority 3: Default fallback
        return "✗ Rendering failed\nPress Ctrl+L to view logs for more details"

    @classmethod
    def from_payload(cls, payload: Any) -> "RenderError | None":
        """Decode error from action payload.

        Expects standardized format: {"error": {"message": ..., "type": ..., "details": ...}}
        """
        if payload is None:
            return None

        if isinstance(payload, dict) and "error" in payload:
            error_data = payload["error"]
            return cls(
                message=error_data.get("message", "Unknown error"),
                error_type=error_data.get("type"),
                details=error_data.get("details"),
            )

        # Unexpected format - log and return generic error
        return cls(message=f"Unexpected error format: {type(payload).__name__}")
