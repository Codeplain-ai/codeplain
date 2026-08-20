"""Tests for the evidential memory record schema.

The invariant under test: status and confidence are *derived* from observations, never
assigned. A record can only claim what the exit codes and the diff actually showed.
"""

import json

import pytest

from memory_management.record import (
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
        test_folder="user_can_add_task",
        test_name="test_add_task_rejects_empty_content",
    )
    defaults.update(overrides)
    return Scope(**defaults)


def make_failure(fingerprint="a3f19c02b1d4"):
    return Failure(
        fingerprint=fingerprint,
        signature="assert <N> == <N>",
        excerpt="assert <N> == <N>",
        exit_code=1,
    )


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


def test_memory_id_and_file_name_are_derived_from_suite_fingerprint_and_attempt():
    record = build_record(
        scope=make_scope(),
        failure=make_failure(fingerprint="a3f19c02b1d4"),
        intervention=make_intervention(attempt_index=3),
        exit_code_after=1,
        fingerprint_after="a3f19c02b1d4",
        observed_at=OBSERVED_AT,
    )

    assert record.memory_id == "conformance-a3f19c02b1d4-03"
    assert record.file_name == "conformance-a3f19c02b1d4-03.json"


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
