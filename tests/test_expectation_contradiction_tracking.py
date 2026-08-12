"""Tests for tracking expectations that the specifications contradict across conformance test fix rounds."""

from unittest.mock import MagicMock

from render_machine.actions.fix_conformance_test import FixConformanceTest
from render_machine.render_types import ConformanceTestsRunningContext

AMENDED_PLAN = "#### Test 1: FullProfileTest\n\nAssert gender has status nodata."


def _running_context(module_name="module", frid="1"):
    return ConformanceTestsRunningContext(
        current_testing_module_name=module_name,
        current_testing_frid=frid,
        fix_attempts=0,
        conformance_tests_json={},
        conformance_tests_render_attempts=0,
        current_testing_frid_specifications=None,
        should_prepare_testing_environment=False,
    )


def _render_context(ctx):
    render_context = MagicMock()
    render_context.conformance_tests_running_context = ctx
    return render_context


def test_unpacks_two_element_response_from_an_older_api():
    reason_code, response_files, issue_analysis = FixConformanceTest._unpack_fix_conformance_tests_issue_result(
        [0, {"a.py": "content"}]
    )

    assert reason_code == 0
    assert response_files == {"a.py": "content"}
    assert issue_analysis == {}


def test_unpacks_three_element_response():
    analysis = {"expectation_contradicts_spec": True, "amended_conformance_tests_plan": None}

    reason_code, response_files, issue_analysis = FixConformanceTest._unpack_fix_conformance_tests_issue_result(
        [1, {}, analysis]
    )

    assert reason_code == 1
    assert issue_analysis == analysis


def test_unpacks_three_element_response_with_null_analysis():
    _, _, issue_analysis = FixConformanceTest._unpack_fix_conformance_tests_issue_result([1, {}, None])

    assert issue_analysis == {}


def test_contradiction_count_accumulates_across_rounds():
    ctx = _running_context()
    render_context = _render_context(ctx)

    for expected_count in (1, 2, 3):
        FixConformanceTest._track_expectation_contradiction(
            render_context, {"expectation_contradicts_spec": True, "amended_conformance_tests_plan": None}
        )
        assert ctx.expectation_contradiction_count == expected_count

    assert ctx.expectation_contradiction_module == "module"
    assert ctx.expectation_contradiction_frid == "1"


def test_contradiction_count_resets_when_a_round_is_not_blamed_on_the_expectation():
    ctx = _running_context()
    ctx.expectation_contradiction_count = 2
    render_context = _render_context(ctx)

    FixConformanceTest._track_expectation_contradiction(render_context, {"expectation_contradicts_spec": False})

    assert ctx.expectation_contradiction_count == 0


def test_contradiction_count_resets_when_the_api_does_not_report_one():
    """An older API returns no analysis at all, which must not be read as a contradiction."""
    ctx = _running_context()
    ctx.expectation_contradiction_count = 2
    render_context = _render_context(ctx)

    FixConformanceTest._track_expectation_contradiction(render_context, {})

    assert ctx.expectation_contradiction_count == 0


def test_amended_plan_replaces_the_pinned_plan_and_clears_the_count():
    ctx = _running_context()
    ctx.current_testing_frid_high_level_implementation_plan = "#### Test 1: FullProfileTest\n\nAssert gender is exact."
    ctx.expectation_contradiction_count = 2
    render_context = _render_context(ctx)

    FixConformanceTest._track_expectation_contradiction(
        render_context,
        {"expectation_contradicts_spec": True, "amended_conformance_tests_plan": AMENDED_PLAN},
    )

    assert ctx.current_testing_frid_high_level_implementation_plan == AMENDED_PLAN
    assert ctx.expectation_contradiction_count == 0


def test_pinned_plan_is_untouched_when_no_amendment_is_returned():
    ctx = _running_context()
    ctx.current_testing_frid_high_level_implementation_plan = "original plan"
    render_context = _render_context(ctx)

    FixConformanceTest._track_expectation_contradiction(
        render_context, {"expectation_contradicts_spec": True, "amended_conformance_tests_plan": None}
    )

    assert ctx.current_testing_frid_high_level_implementation_plan == "original plan"
