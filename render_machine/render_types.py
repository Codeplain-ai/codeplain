from dataclasses import dataclass, field
from typing import Any, Optional

import plain_spec


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
        conformance_test_phase_index: int,
        should_prepare_testing_environment: bool,
        conflicting_requirement_count: int = 0,
        conflicting_module_name: Optional[str] = None,
        conflicting_frid: Optional[str] = None,
    ):
        self.current_testing_module_name = current_testing_module_name
        self.current_testing_frid = current_testing_frid
        self.fix_attempts = fix_attempts
        self._conformance_tests_json = {current_testing_module_name: conformance_tests_json}
        self.conformance_tests_render_attempts = conformance_tests_render_attempts
        self.current_testing_frid_specifications = current_testing_frid_specifications
        self.conformance_test_phase_index = conformance_test_phase_index
        self.should_prepare_testing_environment = should_prepare_testing_environment
        self.conflicting_requirement_count = conflicting_requirement_count
        self.conflicting_module_name = conflicting_module_name
        self.conflicting_frid = conflicting_frid
        # will be propagated only when:
        # - current_testing_frid == frid  noqa: E800
        # - conformance_test_phase_index == 0 (conformance tests phase)
        self.regenerating_conformance_tests: bool = False
        self.current_testing_frid_high_level_implementation_plan: Optional[str] = None
        self.previous_conformance_tests_issue_old: Optional[str] = None
        self.previous_conformance_tests_issue_frid: Optional[str] = None
        self.code_diff_files: Optional[dict[str, str]] = None

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
        return self.current_testing_frid_specifications[plain_spec.ACCEPTANCE_TESTS][
            self.conformance_test_phase_index - 1
        ]

    def set_conformance_tests_summary(self, summary: list[dict]):
        self.get_conformance_tests_json(self.current_testing_module_name)[self.current_testing_frid][
            "test_summary"
        ] = summary

    def get_context_summary(self) -> dict:
        return {
            "current_testing_module_name": self.current_testing_module_name,
            "current_testing_frid": self.current_testing_frid,
            "fix_attempts": self.fix_attempts,
            "conformance_test_phase_index": self.conformance_test_phase_index,
        }


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
