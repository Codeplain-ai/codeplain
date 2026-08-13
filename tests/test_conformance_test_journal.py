"""Tests for the record of what has already been tried on a functionality's conformance tests."""

import json
import os

import pytest

from conformance_test_journal import (
    DIFF_MAX_CHARS,
    MAX_DISTINCT_ISSUES,
    MIN_REPEATS_TO_REPORT_A_STALL,
    ROUNDS_WITH_FULL_DIFF,
    TARGET_CONFORMANCE_TESTS,
    TARGET_IMPLEMENTATION,
    ConformanceTestJournal,
    build_issue_excerpt,
)


@pytest.fixture
def journal():
    return ConformanceTestJournal("shrinq_response_handling", "1")


def test_a_missing_journal_loads_empty(tmp_path):
    loaded = ConformanceTestJournal.load(str(tmp_path), "module", "1")

    assert loaded.attempts == []
    assert loaded.render_for_prompt() is None


def test_attempts_accumulate_rather_than_replacing_each_other(journal):
    for index in range(5):
        journal.record_attempt(TARGET_IMPLEMENTATION, [f"file_{index}.py"], issue_excerpt="boom")

    assert [attempt["round"] for attempt in journal.attempts] == [1, 2, 3, 4, 5]
    assert journal.attempts[0]["files_changed"] == ["file_0.py"]


def test_the_journal_survives_a_save_and_load_round_trip(tmp_path, journal):
    journal.record_attempt(TARGET_IMPLEMENTATION, ["handler.py"], diff_text="-a\n+b", issue_excerpt="boom")
    journal.save(str(tmp_path))

    reloaded = ConformanceTestJournal.load(str(tmp_path), "shrinq_response_handling", "1")
    assert reloaded.attempts == journal.attempts
    assert reloaded.issues == journal.issues


def test_journals_for_different_functionalities_do_not_collide(tmp_path):
    for frid in ("1", "1.2", "2"):
        entry = ConformanceTestJournal("module", frid)
        entry.record_attempt(TARGET_IMPLEMENTATION, [f"for_{frid}.py"], issue_excerpt="boom")
        entry.save(str(tmp_path))

    assert ConformanceTestJournal.load(str(tmp_path), "module", "1.2").attempts[0]["files_changed"] == ["for_1.2.py"]
    assert len(ConformanceTestJournal.load(str(tmp_path), "module", "2").attempts) == 1


def test_a_frid_with_dots_produces_a_usable_path(tmp_path, journal):
    entry = ConformanceTestJournal("module/with/slashes", "1.2.3")
    entry.record_attempt(TARGET_IMPLEMENTATION, ["a.py"], issue_excerpt="boom")
    entry.save(str(tmp_path))

    assert ConformanceTestJournal.load(str(tmp_path), "module/with/slashes", "1.2.3").attempts


def test_a_corrupt_journal_is_discarded_rather_than_raising(tmp_path, journal):
    path = ConformanceTestJournal.journal_path(str(tmp_path), "module", "1")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as corrupt:
        corrupt.write("{ not json")

    assert ConformanceTestJournal.load(str(tmp_path), "module", "1").attempts == []


def test_deleting_the_journal_removes_it_and_tolerates_it_being_gone(tmp_path, journal):
    journal.record_attempt(TARGET_IMPLEMENTATION, ["a.py"], issue_excerpt="boom")
    journal.save(str(tmp_path))

    journal.delete(str(tmp_path))
    journal.delete(str(tmp_path))

    assert not os.path.exists(ConformanceTestJournal.journal_path(str(tmp_path), "shrinq_response_handling", "1"))


# --- storing each distinct failure once ------------------------------------------------------------------


def test_the_same_failure_across_rounds_is_stored_once(journal):
    for _ in range(6):
        journal.record_attempt(TARGET_IMPLEMENTATION, ["a.py"], issue_signature="sig-a", issue_excerpt="the failure")

    assert list(journal.issues) == ["sig-a"]
    assert len(journal.attempts) == 6


def test_distinct_failures_are_each_kept(journal):
    journal.record_attempt(TARGET_IMPLEMENTATION, ["a.py"], issue_signature="sig-a", issue_excerpt="failure A")
    journal.record_attempt(TARGET_IMPLEMENTATION, ["b.py"], issue_signature="sig-b", issue_excerpt="failure B")

    assert journal.issues == {"sig-a": "failure A", "sig-b": "failure B"}


def test_a_failure_with_no_identity_at_all_still_has_its_text_kept(journal):
    """Only a run that produced no usable output has no identity, and losing its text would defeat the point."""
    journal.record_attempt(TARGET_IMPLEMENTATION, ["a.py"], issue_signature=None, issue_excerpt="failure text")
    journal.record_attempt(TARGET_IMPLEMENTATION, ["b.py"], issue_signature=None, issue_excerpt="failure text")

    assert len(journal.issues) == 2
    assert all(text == "failure text" for text in journal.issues.values())


def test_the_number_of_retained_failures_is_capped(journal):
    for index in range(MAX_DISTINCT_ISSUES + 5):
        journal.record_attempt(
            TARGET_IMPLEMENTATION, ["a.py"], issue_signature=f"sig-{index}", issue_excerpt=f"failure {index}"
        )

    assert len(journal.issues) == MAX_DISTINCT_ISSUES


def test_capping_keeps_the_failures_still_in_play_and_the_one_it_started_on(journal):
    """The most recent failures are what is live; the first is what says where the loop went wrong."""
    for index in range(MAX_DISTINCT_ISSUES + 3):
        journal.record_attempt(
            TARGET_IMPLEMENTATION, ["a.py"], issue_signature=f"sig-{index}", issue_excerpt=f"failure {index}"
        )

    assert f"sig-{MAX_DISTINCT_ISSUES + 2}" in journal.issues
    assert "sig-0" in journal.issues
    assert "sig-2" not in journal.issues


def test_a_repeat_of_an_earlier_failure_is_traced_back_to_it(journal):
    journal.record_attempt(TARGET_IMPLEMENTATION, ["a.py"], issue_signature="sig-a", issue_excerpt="failure A")
    journal.record_attempt(TARGET_IMPLEMENTATION, ["b.py"], issue_signature="sig-b", issue_excerpt="failure B")
    journal.record_attempt(TARGET_IMPLEMENTATION, ["c.py"], issue_signature="sig-a", issue_excerpt="failure A")

    assert journal.first_round_with_same_failure(journal.attempts[0]) is None
    assert journal.first_round_with_same_failure(journal.attempts[1]) is None
    assert journal.first_round_with_same_failure(journal.attempts[2]) == 1


def test_a_failure_recognised_only_by_its_distinctive_lines_still_counts_as_a_repeat(journal):
    """Same failure, text moved on around it - which is what the boilerplate profile buys."""
    journal.record_attempt(
        TARGET_IMPLEMENTATION, ["a.py"], issue_signature="sig-a", issue_excerpt="A", distinctive_signature="d-1"
    )
    journal.record_attempt(
        TARGET_IMPLEMENTATION, ["b.py"], issue_signature="sig-b", issue_excerpt="B", distinctive_signature="d-1"
    )

    assert journal.first_round_with_same_failure(journal.attempts[1]) == 1


def test_a_failure_with_no_identity_is_never_reported_as_a_repeat(journal):
    """A run with no usable output gives no basis for claiming two failures are the same."""
    journal.record_attempt(TARGET_IMPLEMENTATION, ["a.py"], issue_signature=None, issue_excerpt="failure")
    journal.record_attempt(TARGET_IMPLEMENTATION, ["b.py"], issue_signature=None, issue_excerpt="failure")

    assert journal.first_round_with_same_failure(journal.attempts[1]) is None


def test_an_unchanged_failure_is_reported_as_a_stall_naming_where_it_started(journal):
    for _ in range(6):
        journal.record_attempt(TARGET_IMPLEMENTATION, ["a.py"], issue_signature="sig-a", issue_excerpt="A")

    assert journal.unbroken_repeat_run() == (1, 6)


def test_a_failure_that_has_since_moved_on_is_not_reported_as_a_stall(journal):
    for _ in range(5):
        journal.record_attempt(TARGET_IMPLEMENTATION, ["a.py"], issue_signature="sig-a", issue_excerpt="A")
    journal.record_attempt(TARGET_IMPLEMENTATION, ["b.py"], issue_signature="sig-b", issue_excerpt="B")

    first_seen, consecutive = journal.unbroken_repeat_run()
    assert consecutive == 1
    assert "The failure has not changed" not in journal.render_for_prompt()


def test_the_stall_is_stated_before_the_attempts_so_it_cannot_be_missed(journal):
    for _ in range(4):
        journal.record_attempt(TARGET_IMPLEMENTATION, ["a.py"], issue_signature="sig-a", issue_excerpt="A")

    rendered = journal.render_for_prompt()

    assert "The failure has not changed for 4 attempts" in rendered
    assert rendered.index("has not changed") < rendered.index("## Attempt 1")


def test_a_stall_is_not_declared_on_a_single_recurrence(journal):
    """Two in a row is noise, not a pattern."""
    for _ in range(MIN_REPEATS_TO_REPORT_A_STALL - 1):
        journal.record_attempt(TARGET_IMPLEMENTATION, ["a.py"], issue_signature="sig-a", issue_excerpt="A")

    assert "The failure has not changed" not in journal.render_for_prompt()


def test_a_row_whose_failure_text_was_evicted_says_so_rather_than_reading_as_blank(journal):
    for index in range(MAX_DISTINCT_ISSUES + 4):
        journal.record_attempt(
            TARGET_IMPLEMENTATION, ["a.py"], issue_signature=f"sig-{index}", issue_excerpt=f"failure {index}"
        )

    assert "no longer retained" in journal.render_for_prompt()


# --- diffs -----------------------------------------------------------------------------------------------


def test_only_the_recent_rounds_keep_their_diff(journal):
    for index in range(ROUNDS_WITH_FULL_DIFF + 4):
        journal.record_attempt(TARGET_IMPLEMENTATION, ["a.py"], diff_text=f"diff {index}", issue_excerpt="boom")

    with_diff = [attempt["round"] for attempt in journal.attempts if attempt["diff"]]
    assert len(with_diff) == ROUNDS_WITH_FULL_DIFF
    assert max(with_diff) == len(journal.attempts)


def test_older_rounds_keep_the_files_they_touched_even_without_the_diff(journal):
    for index in range(ROUNDS_WITH_FULL_DIFF + 3):
        journal.record_attempt(TARGET_IMPLEMENTATION, [f"file_{index}.py"], diff_text="d", issue_excerpt="boom")

    assert journal.attempts[0]["diff"] is None
    assert journal.attempts[0]["files_changed"] == ["file_0.py"]


def test_a_large_diff_is_truncated(journal):
    journal.record_attempt(TARGET_IMPLEMENTATION, ["a.py"], diff_text="x" * (DIFF_MAX_CHARS * 3), issue_excerpt="boom")

    assert len(journal.attempts[0]["diff"]) == DIFF_MAX_CHARS


def test_a_round_that_changed_nothing_is_still_recorded(journal):
    journal.record_attempt(TARGET_IMPLEMENTATION, [], issue_excerpt="boom")

    assert journal.attempts[0]["files_changed"] == []
    assert "No files were changed." in journal.render_for_prompt()


# --- what the fixer reads --------------------------------------------------------------------------------


def test_the_rendered_journal_names_the_functionality_and_the_attempts(journal):
    journal.record_attempt(TARGET_IMPLEMENTATION, ["handler.py"], issue_excerpt="AssertionError: nodata != exact")
    journal.record_attempt(TARGET_CONFORMANCE_TESTS, ["test_profile.py"], issue_excerpt="AssertionError: still wrong")

    rendered = journal.render_for_prompt()

    assert "functionality 1" in rendered
    assert "shrinq_response_handling" in rendered
    assert "Attempt 1" in rendered and "Attempt 2" in rendered
    assert "handler.py" in rendered and "test_profile.py" in rendered


def test_the_rendered_journal_distinguishes_fixing_tests_from_fixing_the_implementation(journal):
    journal.record_attempt(TARGET_CONFORMANCE_TESTS, ["t.py"], issue_excerpt="boom")
    journal.record_attempt(TARGET_IMPLEMENTATION, ["h.py"], issue_excerpt="boom")

    rendered = journal.render_for_prompt()

    assert f"changed the {TARGET_CONFORMANCE_TESTS}" in rendered
    assert f"changed the {TARGET_IMPLEMENTATION}" in rendered


def test_the_rendered_journal_carries_the_failure_text(journal):
    journal.record_attempt(TARGET_IMPLEMENTATION, ["a.py"], issue_signature="sig", issue_excerpt="E   assert 3 == 4")

    assert "E   assert 3 == 4" in journal.render_for_prompt()


def test_a_repeated_failure_is_spelled_out_instead_of_being_pasted_again(journal):
    """The whole point of keying failures by signature: say it once, then refer back to it."""
    for _ in range(3):
        journal.record_attempt(TARGET_IMPLEMENTATION, ["a.py"], issue_signature="sig", issue_excerpt="E assert 3 == 4")

    rendered = journal.render_for_prompt()

    assert rendered.count("E assert 3 == 4") == 1
    assert "prompted by the same failure as attempt 1" in rendered


def test_the_rendered_journal_tells_the_reader_not_to_repeat_what_failed(journal):
    journal.record_attempt(TARGET_IMPLEMENTATION, ["a.py"], issue_excerpt="boom")

    assert "should not be repeated" in journal.render_for_prompt()


def test_the_rendered_journal_shows_recent_changes(journal):
    journal.record_attempt(TARGET_IMPLEMENTATION, ["a.py"], diff_text="-old_value\n+new_value", issue_excerpt="boom")

    rendered = journal.render_for_prompt()

    assert "The change made:" in rendered
    assert "+new_value" in rendered


def test_a_twenty_round_journal_stays_small(journal):
    """Twenty rounds cycling through three failures should not produce twenty copies of the failure text."""
    failure_text = "E   " + ("assertion detail " * 40)
    for index in range(20):
        journal.record_attempt(
            TARGET_IMPLEMENTATION,
            [f"file_{index}.py"],
            diff_text="d" * 800,
            issue_signature=f"sig-{index % 3}",
            issue_excerpt=failure_text,
        )

    rendered = journal.render_for_prompt()

    assert rendered.count(failure_text) == 3
    assert len(rendered) < 20_000


# --- excerpting ------------------------------------------------------------------------------------------


def test_the_issue_excerpt_is_capped_more_tightly_than_a_standalone_one():
    """Several failure excerpts are live at once, so each is held to a smaller budget."""
    long_output = "\n".join(f"line number {index}" for index in range(500))

    assert len(build_issue_excerpt(long_output)) < 2200


def test_the_issue_excerpt_is_unknown_for_empty_output():
    assert build_issue_excerpt("") is None


def test_the_saved_journal_is_readable_json(tmp_path, journal):
    journal.record_attempt(TARGET_IMPLEMENTATION, ["a.py"], issue_signature="sig", issue_excerpt="boom")
    journal.save(str(tmp_path))

    with open(ConformanceTestJournal.journal_path(str(tmp_path), "shrinq_response_handling", "1")) as saved:
        content = json.load(saved)

    assert content["module"] == "shrinq_response_handling"
    assert content["frid"] == "1"
    assert content["issues"] == {"sig": "boom"}
