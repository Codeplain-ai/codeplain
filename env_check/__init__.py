"""Pre-render verification that the machine can actually build and test the project."""

from env_check.preflight import run_environment_preflight
from env_check.report import format_failure_summary, print_report
from env_check.types import PreflightReport

__all__ = [
    "run_environment_preflight",
    "print_report",
    "format_failure_summary",
    "PreflightReport",
]
