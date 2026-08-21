from dataclasses import dataclass, field
from typing import Any, Optional

from plain2code_exceptions import InternalClientError


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


class ModuleConformanceSuite:
    """One conformance test suite that the module conformance phase has to see pass.

    A module-scoped run has one suite per module: the suites of the required modules (re-run as
    regression) followed by this module's own.
    """

    def __init__(
        self,
        module_name: str,
        folder_name: Optional[str],
        is_own_module: bool,
        frid: Optional[str] = None,
    ):
        self.module_name = module_name
        self.folder_name = folder_name
        self.is_own_module = is_own_module
        # Set only for a suite that a required module rendered per functionality, so that the suite
        # can be reported by the functionality it covers.
        self.frid = frid

    def exists(self) -> bool:
        return self.folder_name is not None

    def require_folder_name(self) -> str:
        """The suite's folder, for the callers that only run once the suite is known to exist."""
        if self.folder_name is None:
            raise InternalClientError(
                f"Internal client error: conformance tests of module {self.module_name} have no folder yet."
            )

        return self.folder_name


class ModuleConformanceTestsRunningContext:
    """State of the module-scoped conformance testing phase.

    Where ConformanceTestsRunningContext walks functionality by functionality (and needs phases to
    track how far along that walk it is), this walks suite by suite: plan once, implement the plan in
    batches, then run each suite in turn. A fix that touched the implementation code restarts the
    sweep from the first suite, because a fix made for one suite can regress another.
    """

    def __init__(self, module_name: str, suites: list[ModuleConformanceSuite]):
        self.module_name = module_name
        self.suites = suites
        self.current_suite_index = 0

        # Planning and implementation of this module's suite.
        self.conformance_tests_plan: Optional[dict] = None
        self.conformance_tests_plan_summary: Optional[str] = None
        self.test_summary: Optional[list[dict]] = None
        self.uncovered_frids: list[str] = []
        self.number_of_batches: int = 0
        self.batches_rendered: int = 0

        # The module's acceptance tests, as (frid, acceptance test) pairs in functionality order.
        # They are implemented into the same suite once the planned tests are in place.
        self.acceptance_tests: list[tuple[str, str]] = []
        self.acceptance_tests_rendered: int = 0

        self.fix_attempts: int = 0
        self.conformance_tests_render_attempts: int = 0
        self.should_prepare_testing_environment: bool = True
        self.implementation_code_updated: bool = False

        self.conflicting_requirement_count: int = 0
        self.conflicting_module_name: Optional[str] = None

        self.previous_conformance_tests_issue_old: Optional[str] = None
        self.previous_conformance_tests_issue_module: Optional[str] = None
        self.code_diff_files: Optional[dict[str, str]] = None

    @property
    def current_suite(self) -> ModuleConformanceSuite:
        return self.suites[self.current_suite_index]

    @property
    def own_suite(self) -> ModuleConformanceSuite:
        return self.suites[-1]

    def has_more_batches_to_render(self) -> bool:
        return self.batches_rendered < self.number_of_batches

    def has_more_acceptance_tests_to_render(self) -> bool:
        return self.acceptance_tests_rendered < len(self.acceptance_tests)

    def next_acceptance_test(self) -> tuple[str, str]:
        """The (frid, acceptance test) pair to implement next."""
        return self.acceptance_tests[self.acceptance_tests_rendered]

    def has_more_tests_to_render(self) -> bool:
        return self.has_more_batches_to_render() or self.has_more_acceptance_tests_to_render()

    def has_next_suite(self) -> bool:
        return self.current_suite_index + 1 < len(self.suites)

    def move_to_next_suite(self) -> None:
        self.current_suite_index += 1

    def restart_suites(self) -> None:
        """Re-run every suite from the first one, after the implementation code changed."""
        self.current_suite_index = 0

    def get_conformance_tests_coverage(self) -> list[dict]:
        """The planned test -> covered functionalities map, for the fixer to reason with."""
        if not self.conformance_tests_plan:
            return []

        return self.conformance_tests_plan.get("test_summary") or []


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
