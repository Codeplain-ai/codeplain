"""Tests for the points where the journal meets the render loop.

Three actions share the work: running the conformance tests keys the failure, fixing them records what was
changed in response, and fixing the unit tests records its own rounds into the same journal so that the two
loops can see each other.
"""

from unittest.mock import MagicMock

import pytest

import failure_signature
from conformance_test_journal import (
    LOOP_CONFORMANCE,
    LOOP_UNIT,
    PHASE_INSIDE_CONFORMANCE_FIX,
    PROMPT_FILE_NAME,
    VERDICT_CONFLICTING_REQUIREMENTS,
    VERDICT_CONFORMANCE_TESTS,
    VERDICT_IMPLEMENTATION_CODE,
    VERDICT_UNIT_TESTS,
    ConformanceTestJournal,
)
from render_machine.actions.fix_conformance_test import FixConformanceTest
from render_machine.actions.fix_unit_tests import FixUnitTests
from render_machine.actions.run_conformance_tests import RunConformanceTests
from render_machine.render_types import ConformanceTestsRunningContext

FAILURE_OUTPUT = (
    "building project\nstarted worker [40218]\nrunning tests\n"
    "E   AssertionError: expected exact, got nodata\n1 failed in 2.41s\n"
)
PASSING_OUTPUT = "building project\nstarted worker [40109]\nrunning tests\n14 passed in 1.98s\n"
UNIT_FAILURE_OUTPUT = (
    "building project\nrunning unit tests\nFAILED test_handler.py::test_returns_error_response\n1 failed\n"
)

REASON_CODE_CONFORMANCE_TESTS = FixConformanceTest.ISSUE_REASON_CODE_CONFORMANCE_TESTS
REASON_CODE_IMPLEMENTATION = FixConformanceTest.ISSUE_REASON_CODE_IMPLEMENTATION_CODE
REASON_CODE_CONFLICTING = FixConformanceTest.ISSUE_REASON_CODE_CONFLICTING_REQUIREMENTS


def _rerun(output):
    """The same run again: same failure, fresh worker pid and a different wall clock time."""
    return output.replace("[40218]", "[57781]").replace("2.41s", "3.07s")


def _with_a_moving_number(output, value):
    """The same run again, behind a setup step that reports a fresh transfer rate every time."""
    return f"  % Total    % Received  Average Speed\n  100  {value}  22500\n{output}"


def _diff(file_name, removed, added):
    return {file_name: f"--- a/{file_name}\n+++ b/{file_name}\n@@ -1,2 +1,2 @@\n-{removed}\n+{added}\n"}


@pytest.fixture
def render_context(tmp_path):
    context = MagicMock()
    context.memory_manager.memory_folder = str(tmp_path)
    context.module_name = "module"
    context.conformance_tests_running_context = ConformanceTestsRunningContext(
        current_testing_module_name="module",
        current_testing_frid="1",
        fix_attempts=0,
        conformance_tests_json={},
        conformance_tests_render_attempts=0,
        current_testing_frid_specifications=None,
        should_prepare_testing_environment=False,
    )
    return context


def _journal(render_context):
    return ConformanceTestJournal.load(render_context.memory_manager.memory_folder, "module", "1")


def _record_conformance_round(render_context, reason_code, code_diff, failure_note=None):
    FixConformanceTest._record_round(render_context, reason_code, code_diff, failure_note)


# --- keying a run ----------------------------------------------------------------------------------------


def test_every_run_is_added_to_the_profile(render_context):
    memory_folder = render_context.memory_manager.memory_folder

    RunConformanceTests._fingerprint_run(render_context, 1, FAILURE_OUTPUT)
    RunConformanceTests._fingerprint_run(render_context, 0, PASSING_OUTPUT)

    assert failure_signature.LineFrequencyProfile.load(memory_folder).run_count == 2


def test_a_passing_run_leaves_no_failure_to_journal(render_context):
    RunConformanceTests._fingerprint_run(render_context, 1, FAILURE_OUTPUT)
    RunConformanceTests._fingerprint_run(render_context, 0, PASSING_OUTPUT)
    ctx = render_context.conformance_tests_running_context

    assert ctx.last_failure_excerpt is None
    assert ctx.last_failure_signature is None
    assert ctx.last_failure_skeleton_signature is None
    assert ctx.last_failure_sketch is None


def test_the_very_first_failing_run_is_both_readable_and_identified(render_context):
    """No profile, no passing run, first run of the first functionality - and still identified."""
    RunConformanceTests._fingerprint_run(render_context, 1, FAILURE_OUTPUT)
    ctx = render_context.conformance_tests_running_context

    assert "AssertionError: expected exact, got nodata" in ctx.last_failure_excerpt
    assert ctx.last_failure_signature is not None
    assert ctx.last_failure_skeleton_signature is not None
    assert ctx.last_failure_sketch
    assert ctx.last_failure_distinctive_signature is None


def test_a_different_failure_is_keyed_differently(render_context):
    RunConformanceTests._fingerprint_run(render_context, 1, FAILURE_OUTPUT)
    first = render_context.conformance_tests_running_context.last_failure_signature

    RunConformanceTests._fingerprint_run(render_context, 1, FAILURE_OUTPUT.replace("nodata", "unknown"))

    assert render_context.conformance_tests_running_context.last_failure_signature != first


# --- recording a conformance fix -------------------------------------------------------------------------


def test_a_fix_to_the_implementation_is_recorded_against_the_failure_that_prompted_it(render_context):
    RunConformanceTests._fingerprint_run(render_context, 1, FAILURE_OUTPUT)

    _record_conformance_round(render_context, REASON_CODE_IMPLEMENTATION, _diff("handler.py", "old", "new"))

    journal = _journal(render_context)
    attempt = journal.attempts[0]
    assert attempt["k"]["verdict"] == VERDICT_IMPLEMENTATION_CODE
    assert attempt["loop"] == LOOP_CONFORMANCE
    assert attempt["phase_context"] == PHASE_INSIDE_CONFORMANCE_FIX
    assert [change["path"] for change in attempt["k"]["files"]] == ["handler.py"]
    assert "+new" in attempt["x"]["diff"]["handler.py"]
    assert journal.failures[attempt["l"]["prompted_by"]]["x"]["evidence"]


def test_a_fix_to_the_tests_is_recorded_as_such(render_context):
    RunConformanceTests._fingerprint_run(render_context, 1, FAILURE_OUTPUT)

    _record_conformance_round(render_context, REASON_CODE_CONFORMANCE_TESTS, _diff("test_profile.py", "a", "b"))

    attempt = _journal(render_context).attempts[0]
    assert attempt["k"]["verdict"] == VERDICT_CONFORMANCE_TESTS
    assert attempt["k"]["files"][0]["role"] == "test"


def test_a_round_that_concluded_the_requirements_conflict_is_recorded_as_that(render_context):
    """Previously this was journalled as an ordinary implementation change, losing the verdict entirely."""
    RunConformanceTests._fingerprint_run(render_context, 1, FAILURE_OUTPUT)

    _record_conformance_round(render_context, REASON_CODE_CONFLICTING, _diff("handler.py", "old", "new"))

    assert _journal(render_context).attempts[0]["k"]["verdict"] == VERDICT_CONFLICTING_REQUIREMENTS


def test_the_description_the_reviewer_supplied_is_kept_with_the_failure(render_context):
    RunConformanceTests._fingerprint_run(render_context, 1, FAILURE_OUTPUT)

    _record_conformance_round(
        render_context,
        REASON_CODE_IMPLEMENTATION,
        _diff("handler.py", "old", "new"),
        failure_note={
            "failure_statement": "the handler returns no data for a known profile",
            "failure_tags": {"failure_phase": "assertion", "blamed_artifact": "implementation"},
            "fix_tags": {"approach": "add_missing_behavior", "expected_effect": "the profile is returned"},
        },
    )

    journal = _journal(render_context)
    failure_note = journal.failures[journal.attempts[0]["l"]["prompted_by"]]
    assert failure_note["x"]["statement"] == "the handler returns no data for a known profile"
    assert failure_note["g"]["failure_phase"] == "assertion"
    assert journal.attempts[0]["g"]["approach"] == "add_missing_behavior"


def test_a_round_with_no_description_still_carries_the_failure_text(render_context):
    """A server that sends no note must cost context, not correctness."""
    RunConformanceTests._fingerprint_run(render_context, 1, FAILURE_OUTPUT)

    _record_conformance_round(render_context, REASON_CODE_IMPLEMENTATION, _diff("handler.py", "old", "new"), None)

    rendered = _journal(render_context).render_for_prompt()
    assert "expected exact, got nodata" in rendered


def test_rounds_accumulate_across_the_fix_loop(render_context):
    for index in range(4):
        RunConformanceTests._fingerprint_run(render_context, 1, FAILURE_OUTPUT)
        _record_conformance_round(render_context, REASON_CODE_IMPLEMENTATION, _diff(f"file_{index}.py", "a", "b"))

    assert len(_journal(render_context).attempts) == 4


def test_a_stalled_loop_is_reported_as_stalled_with_no_passing_run_and_one_functionality(render_context):
    """The case that produced 35 unannotated rows: a module's first functionality, never green."""
    for _ in range(6):
        RunConformanceTests._fingerprint_run(render_context, 1, _rerun(FAILURE_OUTPUT))
        _record_conformance_round(render_context, REASON_CODE_IMPLEMENTATION, _diff("handler.py", "a", "b"))

    journal = _journal(render_context)
    rendered = journal.render_for_prompt()

    assert len(journal.failures) == 1
    assert "has not changed for 6 attempts" in rendered
    assert rendered.count("expected exact, got nodata") == 1
    assert "no longer retained" not in rendered


def test_a_number_that_moves_on_every_run_does_not_hide_the_stall(render_context):
    """The d365 case end to end: curl's transfer rate made every round look like a fresh failure."""
    for index in range(6):
        RunConformanceTests._fingerprint_run(render_context, 1, _with_a_moving_number(FAILURE_OUTPUT, 20766 + index))
        _record_conformance_round(render_context, REASON_CODE_IMPLEMENTATION, _diff("handler.py", "a", "b"))

    journal = _journal(render_context)

    assert len(journal.failures) == 1, "one failure was seen six times behind a moving number"
    assert "has not changed for 6 attempts" in journal.render_for_prompt()


def test_a_long_stalled_loop_does_not_grow_the_journal_by_repeating_itself(render_context):
    """Thirty-five rounds on one failure should cost one copy of it, not thirty-five."""
    for _ in range(35):
        RunConformanceTests._fingerprint_run(render_context, 1, FAILURE_OUTPUT)
        _record_conformance_round(render_context, REASON_CODE_IMPLEMENTATION, _diff("handler.py", "a", "b"))

    journal = _journal(render_context)
    rendered = journal.render_for_prompt()

    assert len(journal.failures) == 1
    assert rendered.count("expected exact, got nodata") == 1
    assert "has not changed for 35 attempts" in rendered
    assert len(rendered) < 14000


def test_a_failure_that_changes_ends_the_reported_stall(render_context):
    for _ in range(4):
        RunConformanceTests._fingerprint_run(render_context, 1, FAILURE_OUTPUT)
        _record_conformance_round(render_context, REASON_CODE_IMPLEMENTATION, _diff("handler.py", "a", "b"))

    RunConformanceTests._fingerprint_run(render_context, 1, FAILURE_OUTPUT.replace("nodata", "unknown"))
    _record_conformance_round(render_context, REASON_CODE_IMPLEMENTATION, _diff("handler.py", "a", "b"))

    rendered = _journal(render_context).render_for_prompt()

    assert "has not changed" not in rendered
    assert "got unknown" in rendered


def test_journals_of_different_functionalities_stay_separate(render_context):
    RunConformanceTests._fingerprint_run(render_context, 1, FAILURE_OUTPUT)
    _record_conformance_round(render_context, REASON_CODE_IMPLEMENTATION, _diff("a.py", "a", "b"))

    render_context.conformance_tests_running_context.current_testing_frid = "2"
    _record_conformance_round(render_context, REASON_CODE_IMPLEMENTATION, _diff("b.py", "a", "b"))

    assert len(_journal(render_context).attempts) == 1
    assert ConformanceTestJournal.load(render_context.memory_manager.memory_folder, "module", "2").attempts


# --- the unit-test loop writing into the same journal -----------------------------------------------------


def test_a_unit_test_fix_is_recorded_in_the_journal_of_the_functionality_under_test(render_context):
    action = FixUnitTests(phase_context=PHASE_INSIDE_CONFORMANCE_FIX)
    journal = action._load_journal(render_context)

    action._record_round(render_context, journal, UNIT_FAILURE_OUTPUT, _diff("handler.py", "new", "old"))

    attempt = _journal(render_context).attempts[0]
    assert attempt["loop"] == LOOP_UNIT
    assert attempt["k"]["verdict"] == VERDICT_UNIT_TESTS
    assert attempt["phase_context"] == PHASE_INSIDE_CONFORMANCE_FIX


def test_the_two_loops_undoing_each_other_is_visible_in_one_journal(render_context):
    """Neither loop could see this before: each kept only its own history."""
    action = FixUnitTests(phase_context=PHASE_INSIDE_CONFORMANCE_FIX)

    for _ in range(3):
        RunConformanceTests._fingerprint_run(render_context, 1, FAILURE_OUTPUT)
        _record_conformance_round(
            render_context, REASON_CODE_IMPLEMENTATION, _diff("handler.py", "return error", "raise Failure")
        )
        journal = action._load_journal(render_context)
        action._record_round(
            render_context, journal, UNIT_FAILURE_OUTPUT, _diff("handler.py", "raise Failure", "return error")
        )

    journal = _journal(render_context)
    digest = journal.analyze()
    rendered = journal.render_for_prompt()

    assert digest["cycle"] is not None
    assert digest["contradiction_pairs"]
    assert "One suite's expectation is being satisfied by breaking the other's" in rendered
    assert digest["rounds_by_loop"] == {LOOP_CONFORMANCE: 3, LOOP_UNIT: 3}


def test_the_unit_test_fixer_is_told_which_changes_were_deliberate(render_context):
    RunConformanceTests._fingerprint_run(render_context, 1, FAILURE_OUTPUT)
    _record_conformance_round(
        render_context,
        REASON_CODE_IMPLEMENTATION,
        _diff("handler.py", "return error", "raise Failure"),
        failure_note={"fix_tags": {"expected_effect": "the failure surfaces as an exception"}},
    )

    action = FixUnitTests(phase_context=PHASE_INSIDE_CONFORMANCE_FIX)
    rendered = action._load_journal(render_context).render_recent_implementation_changes()

    assert "made deliberately" in rendered
    assert "the failure surfaces as an exception" in rendered


def test_outside_a_conformance_loop_the_unit_test_fixer_uses_the_functionality_being_implemented(render_context):
    render_context.conformance_tests_running_context = None
    render_context.frid_context.frid = "3"
    render_context.frid_context.specifications = None

    action = FixUnitTests()
    journal = action._load_journal(render_context)
    action._record_round(render_context, journal, UNIT_FAILURE_OUTPUT, _diff("handler.py", "a", "b"))

    assert ConformanceTestJournal.load(render_context.memory_manager.memory_folder, "module", "3").attempts


# --- the journal is handed over explicitly, never scooped up --------------------------------------------


def test_the_profile_is_not_picked_up_as_a_memory_file(render_context, tmp_path):
    """The profile lives beside the memories; it must not be read as one and sent to the model."""
    from memory_management import MemoryManager

    RunConformanceTests._fingerprint_run(render_context, 1, FAILURE_OUTPUT)

    _, memory_files_content = MemoryManager.fetch_memory_files(str(tmp_path))
    assert memory_files_content == {}


def test_the_journal_is_not_picked_up_as_a_memory_file(render_context, tmp_path):
    """It reaches the fixer through an explicit hand-off, not by being scooped up with the memories."""
    from memory_management import MemoryManager

    RunConformanceTests._fingerprint_run(render_context, 1, FAILURE_OUTPUT)
    _record_conformance_round(render_context, REASON_CODE_IMPLEMENTATION, _diff("a.py", "a", "b"))

    _, memory_files_content = MemoryManager.fetch_memory_files(str(tmp_path))
    assert PROMPT_FILE_NAME not in memory_files_content
