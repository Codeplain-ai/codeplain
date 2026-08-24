"""Tests for the deterministic journal of conformance tests fix attempts."""

import json
import os
import tempfile

import pytest

from conformance_fix_journal import MAX_ISSUE_CHARS, FixAttemptJournal, normalized_diff_hash


@pytest.fixture
def memory_folder():
    with tempfile.TemporaryDirectory() as folder:
        yield folder


@pytest.fixture
def journal(memory_folder):
    return FixAttemptJournal(memory_folder)


def record_full_attempt(journal, module="mod", frid="1", attempt=1, diff=None, summary=None, files=None):
    journal.open_attempt(module, frid, attempt, "tests failed")
    journal.record_fix(
        module,
        frid,
        summary or {"hypothesis": f"hypothesis {attempt}", "approach": f"approach {attempt}"},
        files if files is not None else ["src/app.py"],
        "IMPLEMENTATION_CODE",
        diff if diff is not None else {"src/app.py": f"diff {attempt}"},
    )


class TestNormalizedDiffHash:
    def test_identical_diffs_hash_the_same(self):
        diff = {"a.py": "+ line one\n+ line two"}

        assert normalized_diff_hash(diff) == normalized_diff_hash(dict(diff))

    def test_whitespace_only_differences_are_ignored(self):
        assert normalized_diff_hash({"a.py": "+ line one\n\n  + line two  "}) == normalized_diff_hash(
            {"a.py": "+ line one\n+ line two"}
        )

    def test_different_diffs_hash_differently(self):
        assert normalized_diff_hash({"a.py": "+ line one"}) != normalized_diff_hash({"a.py": "+ line two"})

    def test_empty_diff_has_no_hash(self):
        assert normalized_diff_hash({}) is None
        assert normalized_diff_hash(None) is None


class TestJournalEntries:
    def test_attempt_lifecycle_is_recorded(self, journal):
        journal.open_attempt("mod", "1", 1, "first failure output")
        journal.record_fix(
            "mod",
            "1",
            {"hypothesis": "the root cause", "approach": "the fix"},
            ["src/app.py"],
            "IMPLEMENTATION_CODE",
            {"src/app.py": "diff"},
        )
        journal.record_result("mod", "1", "second failure output", passed=False)

        [entry] = journal._read_entries("mod", "1")
        assert entry["attempt"] == 1
        assert entry["issue_before"] == "first failure output"
        assert entry["hypothesis"] == "the root cause"
        assert entry["approach"] == "the fix"
        assert entry["files_changed"] == ["src/app.py"]
        assert entry["target"] == "IMPLEMENTATION_CODE"
        assert entry["result"] == "conformance tests still failed"
        assert entry["issue_after"] == "second failure output"

    def test_a_passing_result_carries_no_issue(self, journal):
        record_full_attempt(journal)
        journal.record_result("mod", "1", None, passed=True)

        [entry] = journal._read_entries("mod", "1")
        assert entry["result"] == "conformance tests passed"
        assert "issue_after" not in entry

    def test_record_result_without_a_pending_attempt_does_nothing(self, journal):
        journal.record_result("mod", "1", "output", passed=False)

        assert journal._read_entries("mod", "1") == []

    def test_record_result_does_not_overwrite_a_completed_attempt(self, journal):
        record_full_attempt(journal)
        journal.record_result("mod", "1", "first", passed=False)
        journal.record_result("mod", "1", "second", passed=False)

        [entry] = journal._read_entries("mod", "1")
        assert entry["issue_after"] == "first"

    def test_long_issues_are_truncated_to_their_tail(self, journal):
        long_issue = "x" * (MAX_ISSUE_CHARS + 100) + "THE END"
        journal.open_attempt("mod", "1", 1, long_issue)

        [entry] = journal._read_entries("mod", "1")
        assert entry["issue_before"].endswith("THE END")
        assert len(entry["issue_before"]) < len(long_issue)

    def test_missing_summary_leaves_the_entry_without_one(self, journal):
        journal.open_attempt("mod", "1", 1, "failure")
        journal.record_fix("mod", "1", None, ["src/app.py"], "IMPLEMENTATION_CODE", {"src/app.py": "diff"})

        [entry] = journal._read_entries("mod", "1")
        assert "hypothesis" not in entry
        assert "approach" not in entry


class TestDuplicateDetection:
    def test_a_repeated_diff_is_marked_as_a_duplicate(self, journal):
        diff = {"src/app.py": "+ same change"}
        record_full_attempt(journal, attempt=1, diff=diff)
        journal.record_result("mod", "1", "still failing", passed=False)
        record_full_attempt(journal, attempt=2, diff={"src/app.py": "+ same change  "})

        entries = journal._read_entries("mod", "1")
        assert "duplicate_of" not in entries[0]
        assert entries[1]["duplicate_of"] == 1

    def test_a_different_diff_is_not_marked(self, journal):
        record_full_attempt(journal, attempt=1, diff={"src/app.py": "+ change one"})
        journal.record_result("mod", "1", "still failing", passed=False)
        record_full_attempt(journal, attempt=2, diff={"src/app.py": "+ change two"})

        entries = journal._read_entries("mod", "1")
        assert "duplicate_of" not in entries[1]

    def test_attempts_without_a_diff_are_never_duplicates(self, journal):
        record_full_attempt(journal, attempt=1, diff={}, files=[])
        journal.record_result("mod", "1", "still failing", passed=False)
        record_full_attempt(journal, attempt=2, diff={}, files=[])

        entries = journal._read_entries("mod", "1")
        assert "duplicate_of" not in entries[1]


class TestDigest:
    def test_digest_carries_the_compact_history_without_raw_outputs(self, journal):
        record_full_attempt(journal, attempt=1)
        journal.record_result("mod", "1", "still failing", passed=False)
        record_full_attempt(journal, attempt=2)

        digest = journal.build_digest("mod", "1")

        assert [entry["attempt"] for entry in digest] == [1, 2]
        assert digest[0]["result"] == "conformance tests still failed"
        assert digest[0]["hypothesis"] == "hypothesis 1"
        assert digest[1]["approach"] == "approach 2"
        for entry in digest:
            assert "issue_before" not in entry
            assert "issue_after" not in entry
            assert "diff_hash" not in entry

    def test_digest_of_an_unknown_frid_is_empty(self, journal):
        assert journal.build_digest("mod", "99") == []


class TestCollectAndClear:
    def test_collect_all_groups_by_module_and_frid(self, journal):
        record_full_attempt(journal, module="mod", frid="1")
        record_full_attempt(journal, module="other_module", frid="2.1")

        journals = journal.collect_all()

        assert [(j["module"], j["frid"]) for j in journals] == [("mod", "1"), ("other_module", "2.1")]
        assert journals[0]["initial_issue"] == "tests failed"
        assert journals[0]["attempts"][0]["hypothesis"] == "hypothesis 1"

    def test_module_names_containing_double_underscores_are_parsed(self, journal):
        record_full_attempt(journal, module="my__module", frid="3")

        [collected] = journal.collect_all()
        assert collected["module"] == "my__module"
        assert collected["frid"] == "3"

    def test_collect_all_without_journals_is_empty(self, journal):
        assert journal.collect_all() == []

    def test_clear_all_removes_every_journal(self, journal):
        record_full_attempt(journal, module="mod", frid="1")
        record_full_attempt(journal, module="mod", frid="2")

        journal.clear_all()

        assert journal.collect_all() == []
        assert not os.path.exists(journal.journal_folder)

    def test_malformed_lines_are_skipped(self, journal, memory_folder):
        record_full_attempt(journal)
        with open(journal._journal_path("mod", "1"), "a") as journal_file:
            journal_file.write("not json\n")
            journal_file.write(json.dumps({"attempt": 2, "issue_before": "x"}) + "\n")

        entries = journal._read_entries("mod", "1")
        assert [entry["attempt"] for entry in entries] == [1, 2]
