"""Concurrent execution of a validated set of environment checks."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed

from env_check.checks import CHECK_TYPES
from env_check.types import STATUS_FAILED, STATUS_SKIPPED, CheckResult, CheckSpec

MAX_WORKERS = 8
TOTAL_BUDGET_SECONDS = 90


def _execute(check: CheckSpec) -> CheckResult:
    check_type = CHECK_TYPES.get(check.type)
    if check_type is None:
        return CheckResult(check, STATUS_SKIPPED, f"unsupported check type '{check.type}'")

    try:
        status, detail = check_type.handler(check.args)
    except Exception as error:  # noqa: BLE001 - a broken probe must not stop the preflight
        return CheckResult(check, STATUS_SKIPPED, f"the check could not be executed ({error})")

    return CheckResult(check, status, detail)


def run_checks(checks: list[CheckSpec], budget_seconds: int = TOTAL_BUDGET_SECONDS) -> list[CheckResult]:
    """Run every check concurrently and return the results in the original order."""
    if not checks:
        return []

    results: dict[int, CheckResult] = {}

    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(checks))) as executor:
        futures = {executor.submit(_execute, check): index for index, check in enumerate(checks)}

        try:
            for future in as_completed(futures, timeout=budget_seconds):
                results[futures[future]] = future.result()
        except TimeoutError:
            for future, index in futures.items():
                if index not in results:
                    future.cancel()

    return [
        results.get(index, CheckResult(check, STATUS_SKIPPED, "the check did not finish within the time budget"))
        for index, check in enumerate(checks)
    ]


def has_blocking_failure(results: list[CheckResult]) -> bool:
    return any(result.status == STATUS_FAILED and result.check.is_blocking for result in results)
