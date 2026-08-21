"""What the renderer reports when a fix loop gives up.

The conformance-fix loop reports exhaustion as a typed, actionable error. The two
client-side loops that can also give up — re-rendering a functionality because its unit
tests never pass, and refactoring — did not: one returned no payload at all (the user saw
`ERROR codeplain: None`), the other built its message without an f-string prefix and
showed literal braces. Both are user-facing render outcomes, so both carry a reason.
"""

from unittest.mock import MagicMock

from render_machine.actions.fix_conformance_test import (
    MAX_CONFORMANCE_TEST_FIX_ATTEMPTS,
    MAX_CONFORMANCE_TEST_RERENDER_ATTEMPTS,
    FixConformanceTest,
)
from render_machine.actions.refactor_code import MAX_REFACTORING_ITERATIONS, RefactorCode
from render_machine.actions.render_functional_requirement import (
    MAX_CODE_GENERATION_RETRIES,
    RenderFunctionalRequirement,
)
from render_machine.render_types import FIX_LOOP_EXHAUSTED_HINT


def exhausted_context(**frid_attributes):
    context = MagicMock()
    context.frid_context.frid = "1"
    context.last_error_message = None
    for name, value in frid_attributes.items():
        setattr(context.frid_context, name, value)
    return context


def exhausted_conformance_context():
    """A conformance loop that has spent its fix attempts and its one regeneration."""
    context = exhausted_context()
    ctx = context.conformance_tests_running_context
    ctx.fix_attempts = MAX_CONFORMANCE_TEST_FIX_ATTEMPTS - 1
    ctx.conformance_tests_render_attempts = MAX_CONFORMANCE_TEST_RERENDER_ATTEMPTS
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
    assert FIX_LOOP_EXHAUSTED_HINT in message


def test_no_fix_loop_blames_the_specification_for_giving_up():
    """Exhaustion tells us we did not converge, not that the input was wrong. Both loops used
    to end with `Please review and rewrite the specification.`, which asserts a cause nothing
    in the render establishes - a conformance loop has been observed patching thirteen times
    against a failure it was not moving."""
    unit = exhausted_context(functional_requirement_render_attempts=MAX_CODE_GENERATION_RETRIES)
    _, unit_payload = RenderFunctionalRequirement().execute(unit, None)

    conformance = exhausted_conformance_context()
    _, conformance_payload = FixConformanceTest().execute(conformance, None)

    for payload in (unit_payload, conformance_payload):
        assert "rewrite the specification" not in payload["error"]["message"]


def test_conformance_exhaustion_names_the_functionality_and_what_to_do():
    context = exhausted_conformance_context()

    outcome, payload = FixConformanceTest().execute(context, None)
    message = payload["error"]["message"]

    assert outcome == FixConformanceTest.LIMIT_EXCEEDED_OUTCOME
    assert payload["error"]["type"] == "CONFORMANCE_TESTS_FIX_EXHAUSTED"
    assert "'1'" in message
    assert "conformance tests" in message
    assert str(MAX_CONFORMANCE_TEST_FIX_ATTEMPTS) in message
    assert FIX_LOOP_EXHAUSTED_HINT in message


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
