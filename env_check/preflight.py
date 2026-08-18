"""The pre-render environment preflight.

Two kinds of check make up a preflight. **Static** checks are the fixed set the
client always runs, derived on its own from the configuration. **Dynamic** checks
are planned per project by the server from the specs, the linked resources and
the testing scripts; the client decides what it is willing to execute and
executes it.

A preflight that cannot reach the server still runs its static checks, and a
preflight that fails for its own reasons never blocks a render -- only a failed
``error`` severity check does.
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
    console.info("Checking that this machine can build and test the project...")

    static_checks, static_advisories = build_static_plan(args)
    plan, unavailable_reason = _request_plan(codeplainAPI, plain_module, args, run_state)

    if unavailable_reason is not None:
        console.debug(
            f"No dynamic checks were planned for this project: {unavailable_reason}. "
            f"Running the {len(static_checks)} static checks only."
        )
    else:
        console.debug(
            f"Running {len(static_checks)} static checks and {len(plan.checks)} dynamic checks "
            f"planned for this project."
        )

    results = run_checks(static_checks + plan.checks)

    return PreflightReport(
        results=results,
        advisories=static_advisories + plan.advisories,
        dropped=plan.dropped,
        plan_unavailable_reason=unavailable_reason,
    )
