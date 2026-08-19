"""Tests for the record of what has already been tried while fixing a functionality's tests.

The record has to answer three questions to be worth its place in a prompt: is this failure one we have seen
before, is this fix one we have already made, and what actually happened as a result. The tests below are
organised around those three, plus what the record costs to carry.
"""

import json

import pytest

import failure_signature
from conformance_test_journal import (
    JOURNAL_VERSION,
    LOOP_CONFORMANCE,
    LOOP_UNIT,
    MAX_FAILURE_NOTES,
    VERDICT_CONFLICTING_REQUIREMENTS,
    VERDICT_CONFORMANCE_TESTS,
    VERDICT_IMPLEMENTATION_CODE,
    VERDICT_UNIT_TESTS,
    ConformanceTestJournal,
    build_issue_excerpt,
    compute_spec_hash,
    describe_change,
)

BOILERPLATE = "\n".join(f"[INFO] building module part {index}" for index in range(20))


def failure(summary, loop=LOOP_CONFORMANCE, exit_code=1, preamble=""):
    """The keys a failing run contributes, computed the way the render loop computes them."""
    output = f"{preamble}{BOILERPLATE}\n{summary}\n"
    return {
        "loop": loop,
        "exit_code": exit_code,
        "exact_signature": failure_signature.compute_exact_signature(output, exit_code),
        "skeleton_signature": failure_signature.compute_skeleton_signature(output, exit_code),
        "sketch": failure_signature.compute_sketch(output),
        "evidence": build_issue_excerpt(output),
    }


def diff(file_name, removed, added):
    return {file_name: f"--- a/{file_name}\n+++ b/{file_name}\n@@ -1,2 +1,2 @@\n-{removed}\n+{added}\n"}


ASSERTION_FAILED = "E   AssertionError: objectExists returned true for a path never uploaded"
UNIT_FAILED = "FAILED FunctionExecutorTest.testReturnsErrorResponseOnFailure"

THROWS = diff("FunctionExecutor.java", "return new ErrorResponse(e);", "throw new RuntimeException(url);")
RETURNS = diff("FunctionExecutor.java", "throw new RuntimeException(url);", "return new ErrorResponse(e);")


@pytest.fixture
def journal():
    return ConformanceTestJournal("module", "1.2", spec_hash="spec-v1")


def oscillate(journal, cycles):
    """The two fix loops trading the same line back and forth, once per cycle."""
    for _ in range(cycles):
        conformance = journal.record_failure(**failure(ASSERTION_FAILED))
        journal.record_attempt(LOOP_CONFORMANCE, VERDICT_IMPLEMENTATION_CODE, THROWS, prompted_by=conformance)
        unit = journal.record_failure(**failure(UNIT_FAILED, loop=LOOP_UNIT))
        journal.record_attempt(LOOP_UNIT, VERDICT_UNIT_TESTS, RETURNS, prompted_by=unit)


# --- persistence -----------------------------------------------------------------------------------------


def test_a_missing_journal_loads_empty(tmp_path):
    journal = ConformanceTestJournal.load(str(tmp_path), "module", "1")
    assert journal.attempts == []
    assert journal.failures == {}
    assert journal.render_for_prompt() is None


def test_the_journal_survives_a_save_and_load_round_trip(tmp_path, journal):
    note = journal.record_failure(**failure(ASSERTION_FAILED))
    journal.record_attempt(LOOP_CONFORMANCE, VERDICT_IMPLEMENTATION_CODE, THROWS, prompted_by=note)
    journal.save(str(tmp_path))

    reloaded = ConformanceTestJournal.load(str(tmp_path), "module", "1.2", spec_hash="spec-v1")
    assert len(reloaded.attempts) == 1
    assert reloaded.attempts[0]["k"]["verdict"] == VERDICT_IMPLEMENTATION_CODE
    assert reloaded.failures[note]["x"]["evidence"]


def test_journals_for_different_functionalities_do_not_collide(tmp_path):
    for frid in ("1", "2"):
        journal = ConformanceTestJournal("module", frid)
        journal.record_attempt(LOOP_CONFORMANCE, VERDICT_IMPLEMENTATION_CODE, diff(f"{frid}.java", "a", "b"))
        journal.save(str(tmp_path))

    first = ConformanceTestJournal.load(str(tmp_path), "module", "1")
    assert list(first.attempts[0]["x"]["diff"]) == ["1.java"]


def test_a_frid_with_dots_produces_a_usable_path(tmp_path, journal):
    journal.save(str(tmp_path))
    assert ConformanceTestJournal.load(str(tmp_path), "module", "1.2").frid == "1.2"


def test_a_corrupt_journal_is_discarded_rather_than_raising(tmp_path, journal):
    journal.record_attempt(LOOP_CONFORMANCE, VERDICT_IMPLEMENTATION_CODE, THROWS)
    journal.save(str(tmp_path))
    with open(ConformanceTestJournal.journal_path(str(tmp_path), "module", "1.2"), "w", encoding="utf-8") as broken:
        broken.write("{not json")

    assert ConformanceTestJournal.load(str(tmp_path), "module", "1.2").attempts == []


def test_deleting_the_journal_removes_it_and_tolerates_it_being_gone(tmp_path, journal):
    journal.record_attempt(LOOP_CONFORMANCE, VERDICT_IMPLEMENTATION_CODE, THROWS)
    journal.save(str(tmp_path))
    journal.delete(str(tmp_path))
    journal.delete(str(tmp_path))

    assert ConformanceTestJournal.load(str(tmp_path), "module", "1.2").attempts == []


def test_the_saved_journal_is_readable_json(tmp_path, journal):
    note = journal.record_failure(**failure(ASSERTION_FAILED))
    journal.record_attempt(LOOP_CONFORMANCE, VERDICT_IMPLEMENTATION_CODE, THROWS, prompted_by=note)
    journal.save(str(tmp_path))

    with open(ConformanceTestJournal.journal_path(str(tmp_path), "module", "1.2"), encoding="utf-8") as saved:
        content = json.load(saved)

    assert content["version"] == JOURNAL_VERSION
    assert content["spec_hash"] == "spec-v1"
    assert content["attempts"][0]["l"]["prompted_by"] == note


# --- a journal is only believed while it still describes the same specification --------------------------


def test_a_journal_from_an_earlier_format_is_discarded(tmp_path, journal):
    journal.record_attempt(LOOP_CONFORMANCE, VERDICT_IMPLEMENTATION_CODE, THROWS)
    journal.save(str(tmp_path))
    path = ConformanceTestJournal.journal_path(str(tmp_path), "module", "1.2")
    with open(path, encoding="utf-8") as saved:
        content = json.load(saved)
    content["version"] = JOURNAL_VERSION - 1
    with open(path, "w", encoding="utf-8") as stale:
        json.dump(content, stale)

    assert ConformanceTestJournal.load(str(tmp_path), "module", "1.2").attempts == []


def test_a_journal_is_discarded_once_the_specification_it_was_written_against_has_changed(tmp_path, journal):
    journal.record_attempt(LOOP_CONFORMANCE, VERDICT_IMPLEMENTATION_CODE, THROWS)
    journal.save(str(tmp_path))

    unchanged = ConformanceTestJournal.load(str(tmp_path), "module", "1.2", spec_hash="spec-v1")
    assert len(unchanged.attempts) == 1

    rewritten = ConformanceTestJournal.load(str(tmp_path), "module", "1.2", spec_hash="spec-v2")
    assert rewritten.attempts == []


def test_the_specification_hash_ignores_the_order_things_are_written_in():
    assert compute_spec_hash({"a": 1, "b": 2}) == compute_spec_hash({"b": 2, "a": 1})
    assert compute_spec_hash(None) is None


# --- recognising the same failure ------------------------------------------------------------------------


def test_the_same_failure_across_rounds_is_recorded_once(journal):
    first = journal.record_failure(**failure(ASSERTION_FAILED))
    journal.record_attempt(LOOP_CONFORMANCE, VERDICT_IMPLEMENTATION_CODE, THROWS, prompted_by=first)
    second = journal.record_failure(**failure(ASSERTION_FAILED))

    assert second == first
    assert len(journal.failures) == 1
    assert journal.failures[first]["seen_count"] == 2


def test_distinct_failures_are_each_kept(journal):
    first = journal.record_failure(**failure(ASSERTION_FAILED))
    second = journal.record_failure(**failure("E   AssertionError: expected 200, got 404"))

    assert first != second
    assert len(journal.failures) == 2


def test_a_number_that_moves_between_runs_does_not_mint_a_new_failure(journal):
    """The d365 case: a curl progress meter in the setup step puts a fresh integer into every run."""
    first = journal.record_failure(**failure(ASSERTION_FAILED, preamble="  % Total 20766  22500\n"))
    second = journal.record_failure(**failure(ASSERTION_FAILED, preamble="  % Total 20794  22500\n"))

    assert second == first
    assert journal.failures[first]["matched_by"] == "skeleton"


def test_a_unit_failure_is_never_the_same_failure_as_a_conformance_one(journal):
    """They share a long build preamble, so by raw similarity they look almost identical."""
    conformance = journal.record_failure(**failure(ASSERTION_FAILED, loop=LOOP_CONFORMANCE))
    unit = journal.record_failure(**failure(ASSERTION_FAILED, loop=LOOP_UNIT))

    assert conformance != unit


def test_a_failure_that_produced_no_output_at_all_matches_nothing(journal):
    first = journal.record_failure(loop=LOOP_CONFORMANCE, exit_code=1)
    second = journal.record_failure(loop=LOOP_CONFORMANCE, exit_code=1)

    assert first != second


def test_a_model_naming_two_failures_alike_relates_them_without_merging_them(journal):
    first = journal.record_failure(**failure(ASSERTION_FAILED), canonical_fingerprint="objectExists is wrong")
    second = journal.record_failure(
        **failure("E   AssertionError: expected 200, got 404"), canonical_fingerprint="objectExists is wrong"
    )

    assert first != second
    assert journal.failures[second]["l"]["same_root_cause_as"] == [first]


# --- recognising the same fix ---------------------------------------------------------------------------


def test_a_change_that_puts_back_what_an_earlier_one_removed_is_recorded_as_a_revert(journal):
    journal.record_attempt(LOOP_CONFORMANCE, VERDICT_IMPLEMENTATION_CODE, THROWS)
    second = journal.record_attempt(LOOP_UNIT, VERDICT_UNIT_TESTS, RETURNS)

    assert journal.attempts[-1]["l"]["reverts"] == "A1"
    assert journal.attempts[-1]["l"]["contradicts"] == "A1"
    assert second == "A2"


def test_a_revert_within_one_loop_is_not_a_contradiction_between_loops(journal):
    journal.record_attempt(LOOP_CONFORMANCE, VERDICT_IMPLEMENTATION_CODE, THROWS)
    journal.record_attempt(LOOP_CONFORMANCE, VERDICT_IMPLEMENTATION_CODE, RETURNS)

    assert journal.attempts[-1]["l"]["reverts"] == "A1"
    assert journal.attempts[-1]["l"]["contradicts"] is None


def test_an_unrelated_change_is_not_a_revert(journal):
    journal.record_attempt(LOOP_CONFORMANCE, VERDICT_IMPLEMENTATION_CODE, THROWS)
    journal.record_attempt(LOOP_CONFORMANCE, VERDICT_CONFORMANCE_TESTS, diff("OtherTest.java", "x", "y"))

    assert journal.attempts[-1]["l"]["reverts"] is None


def test_the_same_change_made_twice_is_recorded_as_the_same_approach(journal):
    journal.record_attempt(LOOP_CONFORMANCE, VERDICT_IMPLEMENTATION_CODE, THROWS)
    journal.record_attempt(LOOP_UNIT, VERDICT_UNIT_TESTS, RETURNS)
    journal.record_attempt(LOOP_CONFORMANCE, VERDICT_IMPLEMENTATION_CODE, THROWS)

    assert journal.attempts[-1]["l"]["same_approach_as"] == ["A1"]


def test_a_change_is_described_the_same_way_whichever_form_the_diff_arrived_in():
    modified = describe_change("Foo.java", "--- a/Foo.java\n+++ b/Foo.java\n@@ -1 +1 @@\n-old();\n+new();\n")
    created = describe_change("Bar.java", "class Bar {}\n")
    deleted = describe_change("Baz.java", "File Baz.java was deleted.")

    assert modified["change"] == "modified" and modified["added"] and modified["removed"]
    assert created["change"] == "created" and created["added"] and not created["removed"]
    assert deleted["change"] == "deleted"


def test_a_test_file_is_told_apart_from_implementation_code():
    assert describe_change("src/FunctionExecutor.java", "a")["role"] == "impl"
    assert describe_change("src/FunctionExecutorTest.java", "a")["role"] == "test"


def test_a_change_that_removes_more_assertions_than_it_adds_is_noticed():
    gutted = describe_change(
        "FooTest.java",
        "--- a/FooTest.java\n+++ b/FooTest.java\n@@ -1,2 +1 @@\n-assertTrue(x);\n-assertEquals(1, y);\n+// removed\n",
    )
    assert gutted["assert_delta"] == -2


def test_a_round_that_changed_nothing_is_still_recorded(journal):
    journal.record_attempt(LOOP_CONFORMANCE, VERDICT_IMPLEMENTATION_CODE, {})

    assert len(journal.attempts) == 1
    assert journal.attempts[0]["k"]["files"] == []
    assert "No files were changed." in journal.render_for_prompt()


def test_the_verdict_that_the_requirements_conflict_is_kept_as_itself(journal):
    journal.record_attempt(LOOP_CONFORMANCE, VERDICT_CONFLICTING_REQUIREMENTS, THROWS)

    assert journal.attempts[0]["k"]["verdict"] == VERDICT_CONFLICTING_REQUIREMENTS


# --- what the shape of the loop says --------------------------------------------------------------------


def test_an_unchanged_failure_is_reported_as_a_stall(journal):
    for _ in range(3):
        note = journal.record_failure(**failure(ASSERTION_FAILED))
        journal.record_attempt(LOOP_CONFORMANCE, VERDICT_IMPLEMENTATION_CODE, THROWS, prompted_by=note)

    assert journal.analyze()["stall_run"] == 3
    assert "has not changed for 3 attempts" in journal.render_for_prompt()


def test_a_stall_is_not_declared_on_a_single_recurrence(journal):
    for _ in range(2):
        note = journal.record_failure(**failure(ASSERTION_FAILED))
        journal.record_attempt(LOOP_CONFORMANCE, VERDICT_IMPLEMENTATION_CODE, THROWS, prompted_by=note)

    assert "has not changed" not in journal.render_for_prompt()


def test_two_failures_trading_places_are_reported_as_a_cycle(journal):
    """An alternation shows no unbroken run, so counting repeats alone cannot see it."""
    oscillate(journal, cycles=3)
    digest = journal.analyze()

    assert digest["stall_run"] < 2
    assert digest["cycle"]["period"] == 2
    assert len(digest["failures_cycling"]) == 2
    assert "alternating between 2 failures" in journal.render_for_prompt()


def test_the_two_loops_undoing_each_other_is_stated_once_however_often_it_happened(journal):
    oscillate(journal, cycles=4)
    rendered = journal.render_for_prompt()

    assert rendered.count("One suite's expectation is being satisfied by breaking the other's") == 1
    assert len(journal.analyze()["contradiction_pairs"]) > 1


def test_rounds_are_counted_per_loop(journal):
    oscillate(journal, cycles=2)

    assert journal.analyze()["rounds_by_loop"] == {LOOP_CONFORMANCE: 2, LOOP_UNIT: 2}


def test_what_a_change_achieved_is_recorded_once_the_next_failure_is_known(journal):
    first = journal.record_failure(**failure(ASSERTION_FAILED))
    journal.record_attempt(LOOP_CONFORMANCE, VERDICT_IMPLEMENTATION_CODE, THROWS, prompted_by=first)
    second = journal.record_failure(**failure("E   AssertionError: expected 200, got 404"))
    journal.record_attempt(LOOP_CONFORMANCE, VERDICT_IMPLEMENTATION_CODE, RETURNS, prompted_by=second)

    assert journal.attempts[0]["l"]["outcome_observed_in"] == second
    assert "the failure became" in journal.render_for_prompt()


def test_a_change_that_left_the_failure_alone_says_so(journal):
    for _ in range(2):
        note = journal.record_failure(**failure(ASSERTION_FAILED))
        journal.record_attempt(LOOP_CONFORMANCE, VERDICT_IMPLEMENTATION_CODE, THROWS, prompted_by=note)

    assert "the failure was unchanged" in journal.render_for_prompt()


def test_rounds_that_weakened_a_test_are_named(journal):
    journal.record_attempt(
        LOOP_CONFORMANCE,
        VERDICT_CONFORMANCE_TESTS,
        {"FooTest.java": "--- a/FooTest.java\n+++ b/FooTest.java\n@@ -1 +0,0 @@\n-assertTrue(x);\n"},
    )

    assert journal.analyze()["assertions_removed_in_rounds"] == [1]
    assert "Attempt 1 removed more assertions" in journal.render_for_prompt()


# --- what the record costs to carry ---------------------------------------------------------------------


def test_the_number_of_retained_failures_is_capped(journal):
    for index in range(MAX_FAILURE_NOTES + 6):
        note = journal.record_failure(**failure(f"E   AssertionError: failure number {index}"))
        journal.record_attempt(LOOP_CONFORMANCE, VERDICT_IMPLEMENTATION_CODE, THROWS, prompted_by=note)

    assert len(journal.failures) <= MAX_FAILURE_NOTES


def test_capping_keeps_the_failures_the_loop_is_still_fighting(journal):
    oscillate(journal, cycles=3)
    cycling = set(journal.analyze()["failures_cycling"])
    for index in range(MAX_FAILURE_NOTES + 6):
        note = journal.record_failure(**failure(f"E   AssertionError: unrelated {index}", loop=LOOP_CONFORMANCE))
        journal.record_attempt(LOOP_CONFORMANCE, VERDICT_CONFORMANCE_TESTS, THROWS, prompted_by=note)

    oscillate(journal, cycles=3)
    assert set(journal.analyze()["failures_cycling"]) <= set(journal.failures)
    assert cycling <= set(journal.failures)


def test_no_attempt_is_left_pointing_at_a_failure_that_is_gone(journal):
    for index in range(MAX_FAILURE_NOTES + 10):
        note = journal.record_failure(**failure(f"E   AssertionError: failure number {index}"))
        journal.record_attempt(LOOP_CONFORMANCE, VERDICT_IMPLEMENTATION_CODE, THROWS, prompted_by=note)

    rendered = journal.render_for_prompt()
    assert "no longer retained" not in rendered
    for attempt in journal.attempts:
        referenced = attempt["l"]["prompted_by"]
        if referenced and referenced in journal.failures:
            assert journal.failures[referenced]["x"] is not None


def test_only_the_recent_rounds_and_the_reverted_ones_keep_their_diff(journal):
    for index in range(12):
        journal.record_attempt(LOOP_CONFORMANCE, VERDICT_CONFORMANCE_TESTS, diff(f"Test{index}.java", "a", "b"))

    assert journal.attempts[0]["x"]["diff"] == {}
    assert journal.attempts[-1]["x"]["diff"]


def test_both_ends_of_a_revert_keep_their_diff(journal):
    journal.record_attempt(LOOP_CONFORMANCE, VERDICT_IMPLEMENTATION_CODE, THROWS)
    journal.record_attempt(LOOP_UNIT, VERDICT_UNIT_TESTS, RETURNS)
    for index in range(10):
        journal.record_attempt(LOOP_CONFORMANCE, VERDICT_CONFORMANCE_TESTS, diff(f"Test{index}.java", "a", "b"))

    assert journal.attempts[0]["x"]["diff"], "the reverted change is no longer on disk anywhere else"
    assert journal.attempts[1]["x"]["diff"]


def test_a_large_diff_is_truncated(journal):
    journal.record_attempt(
        LOOP_CONFORMANCE, VERDICT_CONFORMANCE_TESTS, {"Big.java": "--- a/Big.java\n@@ -1 +1 @@\n" + "+x\n" * 5000}
    )

    assert "truncated" in journal.attempts[0]["x"]["diff"]["Big.java"]


def test_a_long_loop_stays_within_its_budget(journal):
    oscillate(journal, cycles=15)
    rendered = journal.render_for_prompt()

    assert len(rendered) <= 14000, f"the journal grew to {len(rendered)} characters"
    assert "alternating between 2 failures" in rendered


def test_the_verdict_comes_before_the_rows_so_it_cannot_be_missed(journal):
    oscillate(journal, cycles=3)
    rendered = journal.render_for_prompt()

    assert rendered.index("alternating between 2 failures") < rendered.index("## Attempt")


def test_the_rendered_journal_names_the_functionality_and_tells_the_reader_what_it_is_for(journal):
    journal.record_attempt(LOOP_CONFORMANCE, VERDICT_IMPLEMENTATION_CODE, THROWS)
    rendered = journal.render_for_prompt()

    assert "functionality 1.2 in module module" in rendered
    assert "should not be repeated" in rendered


def test_the_rendered_journal_says_which_loop_each_attempt_came_from(journal):
    oscillate(journal, cycles=1)
    rendered = journal.render_for_prompt()

    assert "unit-test fix" in rendered
    assert "conformance-test fix" in rendered


# --- what the unit-test fixer is told --------------------------------------------------------------------


def test_the_deliberate_implementation_changes_are_offered_to_the_unit_test_fixer(journal):
    note = journal.record_failure(**failure(ASSERTION_FAILED))
    journal.record_attempt(
        LOOP_CONFORMANCE,
        VERDICT_IMPLEMENTATION_CODE,
        THROWS,
        prompted_by=note,
        tags={"expected_effect": "the retried URL appears in the thrown exception"},
    )

    rendered = journal.render_recent_implementation_changes()
    assert "made deliberately" in rendered
    assert "the retried URL appears in the thrown exception" in rendered
    assert "FunctionExecutor.java" in rendered


def test_a_change_to_the_conformance_tests_is_not_offered_as_a_deliberate_behaviour_change(journal):
    journal.record_attempt(LOOP_CONFORMANCE, VERDICT_CONFORMANCE_TESTS, diff("FooTest.java", "a", "b"))

    assert journal.render_recent_implementation_changes() is None


def test_there_is_nothing_to_tell_the_unit_test_fixer_before_any_conformance_fix(journal):
    assert journal.render_recent_implementation_changes() is None


# --- excerpting ------------------------------------------------------------------------------------------


def test_the_issue_excerpt_is_capped_more_tightly_than_a_standalone_one():
    excerpt = build_issue_excerpt("\n".join(f"line number {index}" for index in range(500)))

    assert len(excerpt.splitlines()) <= 41
    assert "earlier lines omitted" in excerpt


def test_the_issue_excerpt_is_taken_from_the_failure_not_the_top_of_the_output():
    """The unit-test loop has no reviewer to describe its rounds, so this excerpt is all its notes carry."""
    output = "\n".join(
        [
            "  % Total    % Received % Xferd  Average Speed",
            "openjdk 21.0.8 2025-01-01",
            "[INFO] Scanning for projects...",
        ]
        + [f"[INFO] building part {index}" for index in range(300)]
        + ["[ERROR] HttpClientTest.testRetry:88 expected <200> but was <500>"]
    )

    excerpt = build_issue_excerpt(output)

    assert "expected <200> but was <500>" in excerpt
    assert "Scanning for projects" not in excerpt
    assert "Average Speed" not in excerpt


def test_the_issue_excerpt_is_unknown_for_empty_output():
    assert build_issue_excerpt("") is None


# --- taking back an earlier change ------------------------------------------------------------------------

ADDS_A_DEPENDENCY = {
    "pom.xml": "--- a/pom.xml\n+++ b/pom.xml\n@@ -1,2 +1,4 @@\n+<dependency>logback-classic</dependency>\n+<version>1.5.6</version>\n"
}
REMOVES_IT_AND_MORE = {
    "pom.xml": "--- a/pom.xml\n+++ b/pom.xml\n@@ -1,4 +1,3 @@\n-<dependency>logback-classic</dependency>\n"
    "-<version>1.5.6</version>\n-<version>5.2.1</version>\n-<scope>test</scope>\n+<dependencyManagement/>\n"
}


def test_a_change_that_only_added_lines_is_still_recognised_when_they_are_removed_again(journal):
    """The ordinary shape of a build-config loop, and the one the first implementation could not see: with an
    empty removal set there is no "put back" direction to measure, and requiring one hid every such revert."""
    journal.record_attempt(LOOP_CONFORMANCE, VERDICT_CONFORMANCE_TESTS, ADDS_A_DEPENDENCY)
    journal.record_attempt(LOOP_CONFORMANCE, VERDICT_CONFORMANCE_TESTS, REMOVES_IT_AND_MORE)

    assert journal.attempts[-1]["l"]["reverts"] == "A1"


def test_taking_back_an_earlier_change_counts_even_alongside_other_edits(journal):
    """Containment, not similarity: doing other things at the same time does not make it less of a revert."""
    journal.record_attempt(LOOP_CONFORMANCE, VERDICT_IMPLEMENTATION_CODE, THROWS)
    journal.record_attempt(
        LOOP_CONFORMANCE,
        VERDICT_IMPLEMENTATION_CODE,
        {
            "FunctionExecutor.java": "--- a/FunctionExecutor.java\n+++ b/FunctionExecutor.java\n@@ -1,3 +1,4 @@\n"
            "-throw new RuntimeException(url);\n+return new ErrorResponse(e);\n+log.debug(url);\n+metrics.count();\n"
        },
    )

    assert journal.attempts[-1]["l"]["reverts"] == "A1"


def test_a_round_that_changed_nothing_reverts_nothing(journal):
    journal.record_attempt(LOOP_CONFORMANCE, VERDICT_IMPLEMENTATION_CODE, THROWS)
    journal.record_attempt(LOOP_CONFORMANCE, VERDICT_IMPLEMENTATION_CODE, {})

    assert journal.attempts[-1]["l"]["reverts"] is None


def test_the_same_filename_in_two_projects_is_not_the_same_file(journal):
    """A module and the conformance project that consumes it both have a pom.xml."""
    journal.record_attempt(LOOP_UNIT, VERDICT_UNIT_TESTS, ADDS_A_DEPENDENCY, default_role="impl")
    journal.record_attempt(LOOP_CONFORMANCE, VERDICT_CONFORMANCE_TESTS, REMOVES_IT_AND_MORE, default_role="test")

    assert journal.attempts[-1]["l"]["reverts"] is None
    assert journal.attempts[-1]["l"]["same_approach_as"] == []


# --- what a change achieved -------------------------------------------------------------------------------


def test_a_failure_in_another_suite_is_not_recorded_as_what_this_change_achieved(journal):
    """A unit-test fix followed by a conformance failure has not become that failure - the unit tests passed."""
    unit = journal.record_failure(**failure(UNIT_FAILED, loop=LOOP_UNIT))
    journal.record_attempt(LOOP_UNIT, VERDICT_UNIT_TESTS, RETURNS, prompted_by=unit)
    conformance = journal.record_failure(**failure(ASSERTION_FAILED, loop=LOOP_CONFORMANCE))
    journal.record_attempt(LOOP_CONFORMANCE, VERDICT_IMPLEMENTATION_CODE, THROWS, prompted_by=conformance)

    assert journal.attempts[0]["l"]["outcome_observed_in"] is None
    assert "the failure became" not in journal.render_for_prompt()


def test_capping_the_evidence_keeps_the_end_of_it(journal):
    """A second cut from the front here would drop the very line the excerpt was anchored on."""
    output = "\n".join([f"[INFO] building part {index}" for index in range(300)] + ["[ERROR] testFoo:41 expected 1"])

    note = journal.record_failure(loop=LOOP_CONFORMANCE, exit_code=1, evidence=build_issue_excerpt(output))

    evidence = journal.failures[note]["x"]["evidence"]
    assert "expected 1" in evidence
    assert "earlier lines omitted" in evidence
    assert len(evidence.splitlines()) <= 41
