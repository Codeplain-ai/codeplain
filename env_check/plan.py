"""Validation of the check plan returned by the server.

The plan is untrusted input. Anything that does not validate is dropped with a
recorded reason rather than raising -- a malformed entry must never be able to
stop a render, and an entry the client does not recognise is treated as a
forward-compatible no-op.
"""

from __future__ import annotations

from typing import Any

from env_check.checks import CHECK_TYPES, InvalidCheckArguments
from env_check.types import ORIGIN_DYNAMIC, SEVERITIES, SEVERITY_WARNING, Advisory, CheckPlan, CheckSpec

MAX_CHECKS = 60
MAX_ADVISORIES = 30
MAX_TEXT_LENGTH = 2000


def _clean_text(value: Any, fallback: str = "") -> str:
    if not isinstance(value, str):
        return fallback
    return value.strip()[:MAX_TEXT_LENGTH] or fallback


def _clean_severity(value: Any) -> str:
    if isinstance(value, str) and value.strip().lower() in SEVERITIES:
        return value.strip().lower()
    return SEVERITY_WARNING


def _clean_remediation(value: Any) -> dict[str, str]:
    if isinstance(value, str):
        return {"default": _clean_text(value)} if value.strip() else {}
    if not isinstance(value, dict):
        return {}

    remediation = {}
    for key, hint in value.items():
        if isinstance(key, str) and isinstance(hint, str) and hint.strip():
            remediation[key.strip()] = _clean_text(hint)
    return remediation


def _parse_check(raw: Any, index: int) -> tuple[CheckSpec | None, str | None]:
    if not isinstance(raw, dict):
        return None, f"check #{index} is not an object"

    check_id = _clean_text(raw.get("id"), f"check-{index}")
    check_type = _clean_text(raw.get("type"))

    if check_type not in CHECK_TYPES:
        return None, f"{check_id}: unsupported check type '{check_type}'"

    try:
        args = CHECK_TYPES[check_type].validate(raw.get("args"))
    except InvalidCheckArguments as error:
        return None, f"{check_id}: {error}"

    return (
        CheckSpec(
            id=check_id,
            type=check_type,
            severity=_clean_severity(raw.get("severity")),
            description=_clean_text(raw.get("description"), check_id),
            args=args,
            reason=_clean_text(raw.get("reason")) or None,
            remediation=_clean_remediation(raw.get("remediation")),
            origin=ORIGIN_DYNAMIC,
        ),
        None,
    )


def _parse_advisory(raw: Any, index: int) -> tuple[Advisory | None, str | None]:
    if not isinstance(raw, dict):
        return None, f"advisory #{index} is not an object"

    advisory_id = _clean_text(raw.get("id"), f"advisory-{index}")
    title = _clean_text(raw.get("title"))
    if not title:
        return None, f"{advisory_id}: missing title"

    remediation = _clean_remediation(raw.get("remediation"))

    return (
        Advisory(
            id=advisory_id,
            severity=_clean_severity(raw.get("severity")),
            title=title,
            detail=_clean_text(raw.get("detail")),
            remediation=next(iter(remediation.values()), None),
            origin=ORIGIN_DYNAMIC,
        ),
        None,
    )


def parse_plan(raw: Any) -> CheckPlan:
    """Turn a raw server response into a validated ``CheckPlan``."""
    plan = CheckPlan()
    if not isinstance(raw, dict):
        plan.dropped.append("the server returned a plan that is not an object")
        return plan

    raw_checks = raw.get("checks")
    if isinstance(raw_checks, list):
        for index, raw_check in enumerate(raw_checks[:MAX_CHECKS]):
            check, reason = _parse_check(raw_check, index)
            if check is not None:
                plan.checks.append(check)
            elif reason is not None:
                plan.dropped.append(reason)

        if len(raw_checks) > MAX_CHECKS:
            plan.dropped.append(f"{len(raw_checks) - MAX_CHECKS} checks beyond the limit of {MAX_CHECKS}")

    raw_advisories = raw.get("advisories")
    if isinstance(raw_advisories, list):
        for index, raw_advisory in enumerate(raw_advisories[:MAX_ADVISORIES]):
            advisory, reason = _parse_advisory(raw_advisory, index)
            if advisory is not None:
                plan.advisories.append(advisory)
            elif reason is not None:
                plan.dropped.append(reason)

    return _deduplicate(plan)


def _deduplicate(plan: CheckPlan) -> CheckPlan:
    """Collapse checks that probe exactly the same thing."""
    seen: set[tuple] = set()
    unique_checks = []
    for check in plan.checks:
        fingerprint = (check.type, tuple(sorted((key, str(value)) for key, value in check.args.items())))
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        unique_checks.append(check)

    plan.checks = unique_checks
    return plan
