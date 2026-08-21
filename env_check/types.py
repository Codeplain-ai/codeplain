"""Data types shared by the environment preflight."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

SEVERITY_ERROR = "error"
SEVERITY_WARNING = "warning"
SEVERITIES = (SEVERITY_ERROR, SEVERITY_WARNING)

STATUS_PASSED = "passed"
STATUS_FAILED = "failed"
STATUS_SKIPPED = "skipped"

# Where a check came from. Static checks are the fixed set the client always runs;
# dynamic checks are the ones planned for this particular project.
ORIGIN_STATIC = "static"
ORIGIN_DYNAMIC = "dynamic"


@dataclass
class CheckSpec:
    """A single environment probe the client knows how to execute.

    Instances are produced either by the deterministic static rules or by
    validating a plan returned by the server. A ``CheckSpec`` never carries a
    command line to execute -- only a check ``type`` from the client's registry
    plus validated arguments for it.
    """

    id: str
    type: str
    severity: str
    description: str
    args: dict[str, Any] = field(default_factory=dict)
    reason: Optional[str] = None
    remediation: dict[str, str] = field(default_factory=dict)
    origin: str = ORIGIN_STATIC

    @property
    def is_blocking(self) -> bool:
        return self.severity == SEVERITY_ERROR

    def remediation_for(self, platform_name: str) -> Optional[str]:
        """Return the remediation hint for a platform, falling back to a generic one."""
        if not self.remediation:
            return None
        return (
            self.remediation.get(platform_name)
            or self.remediation.get("default")
            or self.remediation.get("any")
            or next(iter(self.remediation.values()), None)
        )


@dataclass
class Advisory:
    """A finding that needs no probe, e.g. "no script installs dependencies"."""

    id: str
    severity: str
    title: str
    detail: str
    remediation: Optional[str] = None
    origin: str = ORIGIN_STATIC

    @property
    def is_blocking(self) -> bool:
        return self.severity == SEVERITY_ERROR


@dataclass
class CheckPlan:
    checks: list[CheckSpec] = field(default_factory=list)
    advisories: list[Advisory] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)


@dataclass
class CheckResult:
    check: CheckSpec
    status: str
    detail: str

    @property
    def is_blocking_failure(self) -> bool:
        return self.status == STATUS_FAILED and self.check.is_blocking


@dataclass
class PreflightReport:
    results: list[CheckResult] = field(default_factory=list)
    advisories: list[Advisory] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)
    plan_unavailable_reason: Optional[str] = None

    @property
    def blocking_failures(self) -> list[CheckResult]:
        return [result for result in self.results if result.is_blocking_failure]

    @property
    def warnings(self) -> list[CheckResult]:
        return [result for result in self.results if result.status == STATUS_FAILED and not result.check.is_blocking]

    @property
    def passed(self) -> list[CheckResult]:
        return [result for result in self.results if result.status == STATUS_PASSED]

    @property
    def skipped(self) -> list[CheckResult]:
        return [result for result in self.results if result.status == STATUS_SKIPPED]

    @property
    def blocking_advisories(self) -> list[Advisory]:
        return [advisory for advisory in self.advisories if advisory.is_blocking]

    @property
    def has_blocking_findings(self) -> bool:
        return bool(self.blocking_failures) or bool(self.blocking_advisories)

    def count_by_origin(self, origin: str) -> int:
        return sum(1 for result in self.results if result.check.origin == origin)

    def describe_composition(self) -> str:
        """Describe how many of the checks that ran came from each origin.

        This counts every result, passed or not, so it belongs next to the number
        of checks that ran rather than the number that passed.
        """
        return f"{self.count_by_origin(ORIGIN_STATIC)} static, {self.count_by_origin(ORIGIN_DYNAMIC)} dynamic"
