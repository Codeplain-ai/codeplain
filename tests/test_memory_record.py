"""Tests for the evidential memory record schema.

The invariant under test: status and confidence are *derived* from observations, never
assigned. A record can only claim what the exit codes and the diff actually showed.
"""

import json

import pytest

from memory_management.record import (
    MAX_DIFF_LINES,
    SCHEMA_VERSION,
    AttributionConfidence,
    Failure,
    Flag,
    Intervention,
    InterventionTarget,
    MemoryRecord,
    Scope,
    Status,
    Suite,
    Transition,
    bound_diff,
    build_record,
    derive_attribution_confidence,
    derive_status,
    derive_transition,
)

OBSERVED_AT = "2026-08-20T10:14:22Z"


def make_scope(**overrides):
    defaults = dict(
        module="backend",
        frid="2.3",
        testing_module="backend",
        testing_frid="2.1",
        suite=Suite.CONFORMANCE.value,
        test_name="test_add_task_rejects_empty_content",
    )
    defaults.update(overrides)
    return Scope(**defaults)


def make_failure(fingerprint="a3f19c02b1d4"):
    return Failure(fingerprint=fingerprint, causes=["assert 500 == 400"], exit_code=1)


def make_intervention(**overrides):
    defaults = dict(
        attempt_index=3,
        target=InterventionTarget.IMPLEMENTATION.value,
        files_changed=["code/src/tasks.py"],
        lines_changed=7,
        touched_implementation=True,
        touched_test_files=False,
    )
    defaults.update(overrides)
    return Intervention(**defaults)


# --- derivation rules -------------------------------------------------------------


def test_green_run_is_resolved_and_verified():
    transition = derive_transition(exit_code_after=0, fingerprint_before="aaa", fingerprint_after=None)

    assert transition is Transition.RESOLVED
    assert derive_status(transition) is Status.VERIFIED


def test_same_fingerprint_again_is_unchanged_and_refuted():
    transition = derive_transition(exit_code_after=1, fingerprint_before="aaa", fingerprint_after="aaa")

    assert transition is Transition.UNCHANGED
    assert derive_status(transition) is Status.REFUTED


def test_different_fingerprint_is_mutated_and_refuted():
    transition = derive_transition(exit_code_after=1, fingerprint_before="aaa", fingerprint_after="bbb")

    assert transition is Transition.MUTATED
    assert derive_status(transition) is Status.REFUTED


def test_failure_with_nothing_failing_before_is_regressed():
    transition = derive_transition(exit_code_after=1, fingerprint_before=None, fingerprint_after="bbb")

    assert transition is Transition.REGRESSED
    assert derive_status(transition) is Status.REFUTED


@pytest.mark.parametrize(
    "lines_changed,expected",
    [
        (0, AttributionConfidence.HIGH),
        (10, AttributionConfidence.HIGH),
        (11, AttributionConfidence.MEDIUM),
        (50, AttributionConfidence.MEDIUM),
        (51, AttributionConfidence.LOW),
        (400, AttributionConfidence.LOW),
    ],
)
def test_attribution_confidence_falls_off_with_diff_size(lines_changed, expected):
    assert derive_attribution_confidence(lines_changed) is expected


# --- record construction ----------------------------------------------------------


def test_build_record_derives_status_and_confidence():
    record = build_record(
        scope=make_scope(),
        failure=make_failure(),
        intervention=make_intervention(),
        exit_code_after=0,
        fingerprint_after=None,
        observed_at=OBSERVED_AT,
        render_id="render-1",
    )

    assert record.status == Status.VERIFIED.value
    assert record.outcome.transition == Transition.RESOLVED.value
    assert record.attribution_confidence == AttributionConfidence.HIGH.value
    assert record.occurrences == 1
    assert record.flags == []


def test_build_record_flags_interventions_that_edited_tests():
    record = build_record(
        scope=make_scope(),
        failure=make_failure(),
        intervention=make_intervention(
            target=InterventionTarget.CONFORMANCE_TESTS.value,
            touched_implementation=False,
            touched_test_files=True,
        ),
        exit_code_after=0,
        fingerprint_after=None,
        observed_at=OBSERVED_AT,
    )

    # Objectively validated, but suspect: the test passed because the test changed.
    assert record.status == Status.VERIFIED.value
    assert Flag.TEST_FILES_MODIFIED.value in record.flags


def test_memory_id_encodes_suite_and_failure_and_is_a_valid_file_name():
    record = build_record(
        scope=make_scope(),
        failure=make_failure(fingerprint="a3f19c02b1d4"),
        intervention=make_intervention(attempt_index=3),
        exit_code_after=1,
        fingerprint_after="a3f19c02b1d4",
        observed_at=OBSERVED_AT,
    )

    assert record.memory_id.startswith("conformance-a3f19c02b1d4-")
    assert record.file_name == f"{record.memory_id}.json"
    assert "/" not in record.file_name


def test_memory_id_is_the_dedup_key_and_ignores_attempt_index():
    """The file name must identify the attempt, so re-observing it updates one record."""

    def record_for(attempt_index, files_changed):
        return build_record(
            scope=make_scope(),
            failure=make_failure(),
            intervention=make_intervention(attempt_index=attempt_index, files_changed=files_changed),
            exit_code_after=1,
            fingerprint_after="a3f19c02b1d4",
            observed_at=OBSERVED_AT,
        )

    same_attempt_later = record_for(9, ["code/src/tasks.py"])
    baseline = record_for(3, ["code/src/tasks.py"])
    other_intervention = record_for(3, ["code/src/other.py"])

    assert baseline.memory_id == same_attempt_later.memory_id
    assert baseline.memory_id != other_intervention.memory_id


# --- identity and serialization ---------------------------------------------------


def test_dedup_key_matches_for_identical_attempts_and_differs_otherwise():
    base = build_record(
        scope=make_scope(),
        failure=make_failure(),
        intervention=make_intervention(),
        exit_code_after=1,
        fingerprint_after="a3f19c02b1d4",
        observed_at=OBSERVED_AT,
    )
    same_attempt = build_record(
        scope=make_scope(frid="9.9"),
        failure=make_failure(),
        intervention=make_intervention(attempt_index=8),
        exit_code_after=1,
        fingerprint_after="a3f19c02b1d4",
        observed_at="2026-08-20T11:00:00Z",
    )
    other_files = build_record(
        scope=make_scope(),
        failure=make_failure(),
        intervention=make_intervention(files_changed=["code/src/other.py"]),
        exit_code_after=1,
        fingerprint_after="a3f19c02b1d4",
        observed_at=OBSERVED_AT,
    )

    assert base.dedup_key() == same_attempt.dedup_key()
    assert base.dedup_key() != other_files.dedup_key()


def test_json_round_trip_preserves_every_field():
    record = build_record(
        scope=make_scope(),
        failure=make_failure(),
        intervention=make_intervention(),
        exit_code_after=1,
        fingerprint_after="bbb",
        observed_at=OBSERVED_AT,
        render_id="render-1",
    )

    restored = MemoryRecord.from_json(record.to_json())

    assert restored == record


def test_from_json_ignores_unknown_keys():
    record = build_record(
        scope=make_scope(),
        failure=make_failure(),
        intervention=make_intervention(),
        exit_code_after=0,
        fingerprint_after=None,
        observed_at=OBSERVED_AT,
    )
    payload = json.loads(record.to_json())
    payload["some_future_field"] = "ignored"
    payload["scope"]["another_future_field"] = "ignored"

    restored = MemoryRecord.from_json(json.dumps(payload))

    assert restored == record


def test_stored_json_is_human_readable():
    record = build_record(
        scope=make_scope(),
        failure=make_failure(),
        intervention=make_intervention(),
        exit_code_after=0,
        fingerprint_after=None,
        observed_at=OBSERVED_AT,
    )
    raw = record.to_json()

    assert raw.startswith("{\n")
    assert raw.endswith("\n")
    assert '"status": "VERIFIED"' in raw


# --- the failure description ------------------------------------------------------


def test_causes_read_as_one_document_for_scoring_and_display():
    failure = Failure(fingerprint="abc123abc123", causes=["expected: <200> but was: <401>", "and a second cause"])

    assert failure.text == "expected: <200> but was: <401>\nand a second cause"


def test_a_failure_with_no_causes_has_empty_text():
    assert Failure(fingerprint=None).text == ""


# --- the bounded diff -------------------------------------------------------------


def test_the_diff_is_labelled_per_file_and_ordered():
    diff = bound_diff({"src/b.py": "+ second", "src/a.py": "+ first"})

    assert diff.index("--- src/a.py") < diff.index("--- src/b.py")


def test_no_change_means_no_diff():
    assert bound_diff({}) is None


def test_an_oversized_diff_says_how_much_was_left_out():
    """Truncation has to be visible, or a large change reads as a small one."""
    diff = bound_diff({"src/a.py": "\n".join(f"+ line {index}" for index in range(MAX_DIFF_LINES + 20))})

    assert "further diff line(s) not recorded" in diff
    assert len(diff.splitlines()) == MAX_DIFF_LINES + 1


def test_the_diff_is_carried_on_the_record():
    record = build_record(
        scope=make_scope(),
        failure=make_failure(),
        intervention=make_intervention(diff="--- src/a.py\n+ one line"),
        exit_code_after=0,
        fingerprint_after=None,
        observed_at=OBSERVED_AT,
    )

    assert MemoryRecord.from_json(record.to_json()).intervention.diff == "--- src/a.py\n+ one line"


# --- reading an older store -------------------------------------------------------


def test_a_schema_1_record_keeps_its_failure_description():
    """A partial render can hand a store written before the upgrade. Nothing is emptied."""
    legacy = json.dumps(
        {
            "schema_version": 1,
            "memory_id": "conformance-aaa-01",
            "scope": {"module": "backend", "frid": "2.3", "testing_module": "backend", "testing_frid": "2.1"},
            "failure": {
                "fingerprint": "aaaaaaaaaaaa",
                "signature": "assert <N> == <N>\nsecond signature line",
                "excerpt": "a long slice of runner output",
                "exit_code": 1,
            },
            "intervention": {"attempt_index": 1, "target": "IMPLEMENTATION"},
            "outcome": {"exit_code_after": 1, "fingerprint_after": "aaaaaaaaaaaa", "transition": "UNCHANGED"},
            "status": "REFUTED",
            "attribution_confidence": "HIGH",
            "observed_at": OBSERVED_AT,
        }
    )

    record = MemoryRecord.from_json(legacy)

    assert record.failure.causes == ["assert <N> == <N>", "second signature line"]
    assert record.failure.fingerprint == "aaaaaaaaaaaa"
    assert record.intervention.diff is None


def test_new_records_declare_the_current_schema_version():
    record = build_record(
        scope=make_scope(),
        failure=make_failure(),
        intervention=make_intervention(),
        exit_code_after=1,
        fingerprint_after="a3f19c02b1d4",
        observed_at=OBSERVED_AT,
    )

    assert record.schema_version == SCHEMA_VERSION == 2


def test_a_test_name_never_carries_a_filesystem_path():
    """The renderer hands over an absolute path; a record must not keep one."""
    scope = make_scope(test_name="/Users/dev/proj/plain_modules/api/tests/http_client_conformance_tests")

    assert scope.test_name == "http_client_conformance_tests"


def test_a_missing_test_name_stays_missing():
    assert make_scope(test_name=None).test_name is None
