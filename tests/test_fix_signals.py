"""Tests for fix-loop signals: failure fingerprints and duplicate-submission detection."""

from render_machine.fix_signals import compute_failure_signature, find_duplicate_attempt

RUN_A = """
[INFO] Scanning for projects...
[INFO] Running com.example.BatchWorkerConformanceTest
[ERROR] testListPaginationResumeState  Time elapsed: 1.23 s  <<< FAILURE!
java.lang.AssertionError: expected fileCount 1 but was 2
\tat com.example.BatchWorkerConformanceTest.testListPaginationResumeState(BatchWorkerConformanceTest.java:87)
[INFO] Tests run: 7, Failures: 1, Errors: 0, Skipped: 0
[INFO] Total time: 42.7 s
"""

# Same failure, different run: different duration, timestamp, temp path, extra noise.
RUN_A_AGAIN = """
[INFO] 2026-07-08T09:14:22 Building module
[INFO] Output spilled to /var/folders/yv/abc123/T/tmp999.out
[INFO] Running com.example.BatchWorkerConformanceTest
[ERROR] testListPaginationResumeState  Time elapsed: 4.88 s  <<< FAILURE!
java.lang.AssertionError: expected fileCount 1 but was 2
\tat com.example.BatchWorkerConformanceTest.testListPaginationResumeState(BatchWorkerConformanceTest.java:87)
[INFO] Tests run: 7, Failures: 1, Errors: 0, Skipped: 0
[INFO] Total time: 61.2 s
"""

RUN_B = """
[INFO] Running com.example.BatchWorkerConformanceTest
[ERROR] testAcceptanceUpsertDelete  Time elapsed: 2.01 s  <<< ERROR!
org.springframework.beans.factory.UnsatisfiedDependencyException: No qualifying bean of type 'CsvJoiner'
[INFO] Tests run: 7, Failures: 0, Errors: 1, Skipped: 0
"""


def test_signature_stable_across_volatile_noise():
    assert compute_failure_signature(RUN_A) == compute_failure_signature(RUN_A_AGAIN)


def test_signature_differs_for_different_failures():
    assert compute_failure_signature(RUN_A) != compute_failure_signature(RUN_B)


def test_signature_none_when_no_failure_lines():
    assert compute_failure_signature("[INFO] All good\n[INFO] Build succeeded\n") is None
    assert compute_failure_signature("") is None


LEDGER = [
    {
        "root_cause": "CsvJoiner bean is not registered in the Spring context",
        "changes_made": "Added @Component annotation to CsvJoiner and BatchWorker classes",
        "outcome": "conformance tests still failing",
    },
    {
        "root_cause": "Upsert requires describe metadata that is not available",
        "changes_made": "Added an overloaded execute method passing an empty HashMap as the describe object",
        "outcome": "rejected by the integrity reviewer",
    },
    {
        "root_cause": "Pending attempt with no outcome yet",
        "changes_made": "Added an overloaded execute method passing an empty HashMap as the describe object",
        "outcome": "",
    },
]


def test_duplicate_detected_against_failed_entry():
    resubmission = {
        "root_cause": "The CsvJoiner bean is not registered in the Spring context",
        "changes_made": "Added @Component annotation to the CsvJoiner and BatchWorker classes",
    }
    assert find_duplicate_attempt(resubmission, LEDGER) == 0


def test_duplicate_detected_against_rejected_entry():
    resubmission = {
        "root_cause": "Upsert requires describe metadata that is not available",
        "changes_made": "Added overloaded execute method passing an empty HashMap as describe object",
    }
    assert find_duplicate_attempt(resubmission, LEDGER) == 1


def test_different_approach_is_not_flagged():
    fresh = {
        "root_cause": "A duplicate class in the dependency jar shadows the edited copy",
        "changes_made": "Delegated metadata fetching via upsertRecordProcessor.getMetadataFetcher()",
    }
    assert find_duplicate_attempt(fresh, LEDGER) is None


def test_entries_without_outcome_are_ignored():
    # Identical to ledger entry #2, but that entry has no outcome recorded yet —
    # only demonstrably failed attempts may block a submission.
    resubmission = {
        "root_cause": "Pending attempt with no outcome yet",
        "changes_made": "Added an overloaded execute method passing an empty HashMap as the describe object",
    }
    ledger = [LEDGER[2]]
    assert find_duplicate_attempt(resubmission, ledger) is None


def test_short_descriptions_are_not_compared():
    assert find_duplicate_attempt({"root_cause": "fix", "changes_made": "bug"}, LEDGER) is None
