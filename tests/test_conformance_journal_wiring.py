"""Tests for the points where the journal meets the render loop.

Two actions share the work: running the tests fingerprints the failure, and fixing them records what was
changed in response and hands the accumulated record to the fixer.
"""

from unittest.mock import MagicMock

import pytest

import failure_signature
from conformance_test_journal import (
    PROMPT_FILE_NAME,
    TARGET_CONFORMANCE_TESTS,
    TARGET_IMPLEMENTATION,
    ConformanceTestJournal,
)
from render_machine.actions.fix_conformance_test import FixConformanceTest
from render_machine.actions.run_conformance_tests import RunConformanceTests
from render_machine.render_types import ConformanceTestsRunningContext

FAILURE_OUTPUT = (
    "building project\nstarted worker [40218]\nrunning tests\n"
    "E   AssertionError: expected exact, got nodata\n1 failed in 2.41s\n"
)
PASSING_OUTPUT = "building project\nstarted worker [40109]\nrunning tests\n14 passed in 1.98s\n"


def _rerun(output):
    """The same run again: same failure, fresh worker pid and a different wall clock time."""
    return output.replace("[40218]", "[57781]").replace("2.41s", "3.07s")


@pytest.fixture
def render_context(tmp_path):
    context = MagicMock()
    context.memory_manager.memory_folder = str(tmp_path)
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


# --- fingerprinting a run --------------------------------------------------------------------------------


def test_every_run_is_added_to_the_profile(render_context):
    memory_folder = render_context.memory_manager.memory_folder

    RunConformanceTests._fingerprint_run(render_context, 1, FAILURE_OUTPUT)
    RunConformanceTests._fingerprint_run(render_context, 0, PASSING_OUTPUT)

    assert failure_signature.LineFrequencyProfile.load(memory_folder).run_count == 2


def test_a_passing_run_leaves_no_failure_to_journal(render_context):
    RunConformanceTests._fingerprint_run(render_context, 1, FAILURE_OUTPUT)
    RunConformanceTests._fingerprint_run(render_context, 0, PASSING_OUTPUT)

    assert render_context.conformance_tests_running_context.last_failure_excerpt is None
    assert render_context.conformance_tests_running_context.last_failure_signature is None


def test_a_failing_run_leaves_a_readable_excerpt_even_before_the_profile_matures(render_context):
    RunConformanceTests._fingerprint_run(render_context, 1, FAILURE_OUTPUT)
    ctx = render_context.conformance_tests_running_context

    assert "AssertionError: expected exact, got nodata" in ctx.last_failure_excerpt
    assert ctx.last_failure_signature is None


def test_the_same_failure_is_fingerprinted_alike_once_a_run_has_passed(render_context):
    RunConformanceTests._fingerprint_run(render_context, 0, PASSING_OUTPUT)
    RunConformanceTests._fingerprint_run(render_context, 1, FAILURE_OUTPUT)
    first = render_context.conformance_tests_running_context.last_failure_signature

    RunConformanceTests._fingerprint_run(render_context, 1, _rerun(FAILURE_OUTPUT))

    assert first is not None
    assert render_context.conformance_tests_running_context.last_failure_signature == first


def test_a_different_failure_is_fingerprinted_differently(render_context):
    RunConformanceTests._fingerprint_run(render_context, 0, PASSING_OUTPUT)
    RunConformanceTests._fingerprint_run(render_context, 1, FAILURE_OUTPUT)
    first = render_context.conformance_tests_running_context.last_failure_signature

    RunConformanceTests._fingerprint_run(render_context, 1, FAILURE_OUTPUT.replace("nodata", "unknown"))

    assert render_context.conformance_tests_running_context.last_failure_signature != first


def test_a_failure_repeated_all_through_a_fix_loop_stays_recognisable(render_context):
    """The realistic shape: one functionality, one passing run before it, then the same failure over and over."""
    RunConformanceTests._fingerprint_run(render_context, 0, PASSING_OUTPUT)
    signatures = []
    for _ in range(20):
        RunConformanceTests._fingerprint_run(render_context, 1, FAILURE_OUTPUT)
        signatures.append(render_context.conformance_tests_running_context.last_failure_signature)

    assert None not in signatures
    assert len(set(signatures)) == 1


# --- recording a fix -------------------------------------------------------------------------------------


def test_a_fix_to_the_implementation_is_recorded_against_the_failure_that_prompted_it(render_context):
    RunConformanceTests._fingerprint_run(render_context, 1, FAILURE_OUTPUT)

    FixConformanceTest._record_attempt(render_context, TARGET_IMPLEMENTATION, {"handler.py": "-old\n+new"})

    attempt = _journal(render_context).attempts[0]
    assert attempt["target"] == TARGET_IMPLEMENTATION
    assert attempt["files_changed"] == ["handler.py"]
    assert "+new" in attempt["diff"]


def test_a_fix_to_the_tests_is_recorded_as_such(render_context):
    RunConformanceTests._fingerprint_run(render_context, 1, FAILURE_OUTPUT)

    FixConformanceTest._record_attempt(render_context, TARGET_CONFORMANCE_TESTS, {"test_profile.py": "-a\n+b"})

    assert _journal(render_context).attempts[0]["target"] == TARGET_CONFORMANCE_TESTS


def test_rounds_accumulate_across_the_fix_loop(render_context):
    for index in range(4):
        RunConformanceTests._fingerprint_run(render_context, 1, FAILURE_OUTPUT)
        FixConformanceTest._record_attempt(render_context, TARGET_IMPLEMENTATION, {f"file_{index}.py": "d"})

    assert len(_journal(render_context).attempts) == 4


def test_a_recurring_failure_is_reported_back_to_the_fixer_as_a_repeat(render_context):
    """End to end: a fix that does not move the failure is visible as such on the next round."""
    RunConformanceTests._fingerprint_run(render_context, 0, PASSING_OUTPUT)
    for _ in range(4):
        RunConformanceTests._fingerprint_run(render_context, 1, FAILURE_OUTPUT)
        FixConformanceTest._record_attempt(render_context, TARGET_IMPLEMENTATION, {"handler.py": "d"})

    rendered = _journal(render_context).render_for_prompt()

    assert "same one already seen in attempt 1" in rendered
    assert rendered.count("expected exact, got nodata") == 1


def test_without_a_passing_run_the_failure_text_is_still_kept_for_every_round(render_context):
    """No passing run means no signature, so no repeat detection - but never a lost failure."""
    for _ in range(3):
        RunConformanceTests._fingerprint_run(render_context, 1, FAILURE_OUTPUT)
        FixConformanceTest._record_attempt(render_context, TARGET_IMPLEMENTATION, {"handler.py": "d"})

    rendered = _journal(render_context).render_for_prompt()

    assert "same one already seen" not in rendered
    assert rendered.count("expected exact, got nodata") == 3


def test_journals_of_different_functionalities_stay_separate(render_context):
    RunConformanceTests._fingerprint_run(render_context, 1, FAILURE_OUTPUT)
    FixConformanceTest._record_attempt(render_context, TARGET_IMPLEMENTATION, {"a.py": "d"})

    render_context.conformance_tests_running_context.current_testing_frid = "2"
    FixConformanceTest._record_attempt(render_context, TARGET_IMPLEMENTATION, {"b.py": "d"})

    assert len(_journal(render_context).attempts) == 1
    assert ConformanceTestJournal.load(render_context.memory_manager.memory_folder, "module", "2").attempts


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
    FixConformanceTest._record_attempt(render_context, TARGET_IMPLEMENTATION, {"a.py": "d"})

    _, memory_files_content = MemoryManager.fetch_memory_files(str(tmp_path))
    assert PROMPT_FILE_NAME not in memory_files_content
