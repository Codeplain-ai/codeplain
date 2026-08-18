"""What the renderer reports when a fix loop gives up.

The conformance-fix loop reports exhaustion as a typed, actionable error. The two
client-side loops that can also give up — re-rendering a functionality because its unit
tests never pass, and refactoring — did not: one returned no payload at all (the user saw
`ERROR codeplain: None`), the other built its message without an f-string prefix and
showed literal braces. Both are user-facing render outcomes, so both carry a reason.
"""

from unittest.mock import MagicMock

from render_machine.actions.refactor_code import MAX_REFACTORING_ITERATIONS, RefactorCode
from render_machine.actions.render_functional_requirement import (
    MAX_CODE_GENERATION_RETRIES,
    RenderFunctionalRequirement,
)


def exhausted_context(**frid_attributes):
    context = MagicMock()
    context.frid_context.frid = "1"
    context.last_error_message = None
    for name, value in frid_attributes.items():
        setattr(context.frid_context, name, value)
    return context


def test_unit_test_exhaustion_reports_a_typed_reason():
    context = exhausted_context(functional_requirement_render_attempts=MAX_CODE_GENERATION_RETRIES)

    outcome, payload = RenderFunctionalRequirement().execute(context, None)

    assert outcome == RenderFunctionalRequirement.ITERATION_LIMIT_EXCEEDED_OUTCOME
    assert payload is not None, "a render that stopped here used to hand ExitWithError nothing to print"
    assert payload["error"]["type"] == "UNIT_TESTS_FIX_EXHAUSTED"


def test_unit_test_exhaustion_names_the_functionality_and_what_to_do():
    context = exhausted_context(functional_requirement_render_attempts=MAX_CODE_GENERATION_RETRIES)

    _, payload = RenderFunctionalRequirement().execute(context, None)
    message = payload["error"]["message"]

    assert "'1'" in message
    assert "unit tests" in message
    assert "specification" in message


def test_unit_test_exhaustion_logs_the_same_reason_it_returns():
    context = exhausted_context(functional_requirement_render_attempts=MAX_CODE_GENERATION_RETRIES)

    _, payload = RenderFunctionalRequirement().execute(context, None)

    assert context.last_error_message == payload["error"]["message"]


def test_refactoring_exhaustion_interpolates_its_message():
    context = exhausted_context(refactoring_iteration=MAX_REFACTORING_ITERATIONS - 1)

    _, payload = RefactorCode().execute(context, None)
    message = payload["error"]["message"]

    assert "{" not in message, "the message was built without an f-string prefix"
    assert str(MAX_REFACTORING_ITERATIONS) in message
    assert "1" in message
