"""Rendering of the preflight outcome for the user."""

from __future__ import annotations

import sys

from env_check.types import SEVERITY_ERROR, STATUS_SKIPPED, Advisory, CheckResult, PreflightReport
from plain2code_console import MUTED_COLOR, SUCCESS_COLOR, console

HEADER = "Environment preflight"


def _label_for(severity: str) -> str:
    return "FAILED " if severity == SEVERITY_ERROR else "WARNING"


def _emitter_for(severity: str):
    return console.error if severity == SEVERITY_ERROR else console.warning


def _print_finding(severity: str, origin: str, headline: str, lines: list[str]) -> None:
    emit = _emitter_for(severity)
    emit(f"  {_label_for(severity)} {origin:<8}{headline}")
    for line in lines:
        emit(f"      {line}")


def _print_failure(result: CheckResult) -> None:
    lines = [result.detail]

    if result.check.reason:
        lines.append(f"Why it matters: {result.check.reason}")

    remediation = result.check.remediation_for(sys.platform)
    if remediation:
        lines.append(f"Fix: {remediation}")

    _print_finding(result.check.severity, result.check.origin, result.check.description, lines)


def _print_advisory(advisory: Advisory) -> None:
    lines = []
    if advisory.detail:
        lines.append(advisory.detail)
    if advisory.remediation:
        lines.append(f"Fix: {advisory.remediation}")

    _print_finding(advisory.severity, advisory.origin, advisory.title, lines)


def print_report(report: PreflightReport, verbose: bool = False) -> None:
    """Print the preflight outcome, in full detail when something went wrong."""
    blocking = report.blocking_failures
    warnings = report.warnings
    blocking_advisories = report.blocking_advisories
    warning_advisories = [advisory for advisory in report.advisories if advisory.severity != SEVERITY_ERROR]

    has_findings = bool(blocking or warnings or blocking_advisories or warning_advisories)

    if not has_findings and not verbose:
        console.info(
            f"✓ {HEADER}: {len(report.passed)} checks passed ({report.describe_composition()}).",
            color=SUCCESS_COLOR,
        )
        _print_skipped(report, verbose)
        return

    console.info(f"\n{HEADER}\n")

    if verbose:
        for result in report.passed:
            console.info(
                f"  OK      {result.check.origin:<8}{result.check.description} — {result.detail}",
                color=MUTED_COLOR,
            )

    for result in blocking:
        _print_failure(result)
    for advisory in blocking_advisories:
        _print_advisory(advisory)
    for result in warnings:
        _print_failure(result)
    for advisory in warning_advisories:
        _print_advisory(advisory)

    console.info(
        f"\n  {len(report.passed)} passed ({report.describe_composition()}), "
        f"{len(blocking) + len(blocking_advisories)} blocking, "
        f"{len(warnings) + len(warning_advisories)} warnings.\n"
    )

    _print_skipped(report, verbose)


def _print_skipped(report: PreflightReport, verbose: bool) -> None:
    if report.plan_unavailable_reason:
        console.debug(f"{HEADER}: {report.plan_unavailable_reason}")

    for reason in report.dropped:
        console.debug(f"{HEADER}: dropped a check — {reason}")

    for result in report.skipped:
        if verbose:
            console.warning(f"  SKIPPED {result.check.origin:<8}{result.check.description} — {result.detail}")
        else:
            console.debug(f"{HEADER}: skipped {result.check.id} — {result.detail}")

    if report.skipped and not verbose:
        console.debug(f"{HEADER}: {len(report.skipped)} checks were skipped. Run with --verbose for details.")


def format_failure_summary(report: PreflightReport) -> str:
    """Build the message carried by ``EnvironmentCheckFailed``."""
    lines = ["The environment is not ready to render:"]

    for result in report.blocking_failures:
        lines.append(f"  - {result.check.description}: {result.detail}")
        remediation = result.check.remediation_for(sys.platform)
        if remediation:
            lines.append(f"    Fix: {remediation}")

    for advisory in report.blocking_advisories:
        lines.append(f"  - {advisory.title}: {advisory.detail}")
        if advisory.remediation:
            lines.append(f"    Fix: {advisory.remediation}")

    lines.append("")
    lines.append("Fix the issues above and render again, or pass --skip-env-check to render anyway.\n")
    return "\n".join(lines)


__all__ = ["print_report", "format_failure_summary", "STATUS_SKIPPED"]
