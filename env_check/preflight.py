"""The pre-render environment preflight.

The server plans which checks make sense for a project; the client decides what
it is willing to execute and executes it. A preflight that cannot reach the
server still runs its deterministic layer, and a preflight that fails for its own
reasons never blocks a render -- only a failed ``error`` severity check does.
"""

from __future__ import annotations

from typing import Optional

from env_check.context import build_environment_context
from env_check.plan import parse_plan
from env_check.runner import run_checks
from env_check.static_rules import build_static_plan
from env_check.types import CheckPlan, PreflightReport
from plain2code_console import console
from plain_modules import PlainModule


def _request_plan(codeplainAPI, plain_module: PlainModule, args, run_state) -> tuple[CheckPlan, Optional[str]]:
    """Ask the server for a check plan. Returns an empty plan when unavailable."""
    try:
        context = build_environment_context(plain_module, args)
    except Exception as error:  # noqa: BLE001 - context assembly must never break a render
        return CheckPlan(), f"the project context could not be assembled ({error})"

    try:
        response = codeplainAPI.check_environment(context, run_state)
    except Exception as error:  # noqa: BLE001 - the planned layer is best effort
        return CheckPlan(), f"the server could not plan the checks ({error})"

    if response is None:
        return CheckPlan(), "the server did not return a check plan"

    return parse_plan(response), None


def run_environment_preflight(codeplainAPI, plain_module: PlainModule, args, run_state) -> PreflightReport:
    """Run the environment preflight and return its report."""
    console.debug("Running the environment preflight...")

    static_checks, static_advisories = build_static_plan(args)
    plan, unavailable_reason = _request_plan(codeplainAPI, plain_module, args, run_state)

    results = run_checks(static_checks + plan.checks)

    return PreflightReport(
        results=results,
        advisories=static_advisories + plan.advisories,
        dropped=plan.dropped,
        plan_unavailable_reason=unavailable_reason,
    )
