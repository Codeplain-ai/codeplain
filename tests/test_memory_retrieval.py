"""Tests for ranked retrieval over memory records.

What matters here: an exact fingerprint match outranks everything, verified records come
before refuted ones for the same failure, depth scales with how stuck the fix loop is,
and the mode filter can exclude whole status classes for benchmarking.
"""

import pytest

from memory_management.record import Failure, Intervention, InterventionTarget, Scope, Status, Suite, build_record
from memory_management.retrieval import MemoryMode, rank_records, retrieval_depth, select_records

OBSERVED_AT = "2026-08-20T10:14:22Z"

TARGET_FINGERPRINT = "aaaaaaaaaaaa"
OTHER_FINGERPRINT = "bbbbbbbbbbbb"


def make_record(
    fingerprint=TARGET_FINGERPRINT,
    signature="assert <N> == <N> in add task validation",
    test_name="test_add_task_rejects_empty_content",
    files_changed=("code/src/tasks.py",),
    resolved=False,
    occurrences=1,
):
    record = build_record(
        scope=Scope(
            module="backend",
            frid="2.3",
            testing_module="backend",
            testing_frid="2.1",
            suite=Suite.CONFORMANCE.value,
            test_name=test_name,
        ),
        failure=Failure(fingerprint=fingerprint, signature=signature, excerpt=signature, exit_code=1),
        intervention=Intervention(
            attempt_index=1,
            target=InterventionTarget.IMPLEMENTATION.value,
            files_changed=list(files_changed),
            lines_changed=5,
            touched_implementation=True,
        ),
        exit_code_after=0 if resolved else 1,
        fingerprint_after=None if resolved else fingerprint,
        observed_at=OBSERVED_AT,
    )
    record.occurrences = occurrences
    return record


# --- adaptive depth ---------------------------------------------------------------


@pytest.mark.parametrize(
    "fix_attempts,expected",
    [(0, 0), (1, 3), (2, 3), (3, 6), (5, 6), (6, 12), (19, 12)],
)
def test_depth_scales_with_fix_attempts(fix_attempts, expected):
    assert retrieval_depth(fix_attempts) == expected


def test_first_pass_retrieves_nothing():
    records = [make_record(resolved=True)]

    assert select_records(records, depth=retrieval_depth(0), fingerprint=TARGET_FINGERPRINT) == []


def test_depth_caps_the_number_of_records():
    records = [make_record(files_changed=(f"code/src/f{index}.py",)) for index in range(20)]

    selected = select_records(records, depth=6, fingerprint=TARGET_FINGERPRINT)

    assert len(selected) == 6


# --- ranking ----------------------------------------------------------------------


def test_verified_same_fingerprint_outranks_refuted_same_fingerprint():
    refuted = make_record(files_changed=("code/src/a.py",), resolved=False)
    verified = make_record(files_changed=("code/src/b.py",), resolved=True)

    ranked = rank_records([refuted, verified], fingerprint=TARGET_FINGERPRINT)

    assert [record.status for record in ranked] == [Status.VERIFIED.value, Status.REFUTED.value]


def test_exact_fingerprint_match_outranks_same_test_name():
    same_test_other_failure = make_record(fingerprint=OTHER_FINGERPRINT, files_changed=("code/src/a.py",))
    exact_match = make_record(files_changed=("code/src/b.py",))

    ranked = rank_records(
        [same_test_other_failure, exact_match],
        fingerprint=TARGET_FINGERPRINT,
        test_name="test_add_task_rejects_empty_content",
    )

    assert ranked[0].memory_id == exact_match.memory_id


def test_same_test_name_outranks_file_overlap_only():
    file_overlap_only = make_record(
        fingerprint=OTHER_FINGERPRINT,
        test_name="test_something_else",
        files_changed=("code/src/tasks.py",),
    )
    same_test = make_record(
        fingerprint=OTHER_FINGERPRINT,
        test_name="test_add_task_rejects_empty_content",
        files_changed=("code/src/unrelated.py",),
    )

    ranked = rank_records(
        [file_overlap_only, same_test],
        fingerprint=TARGET_FINGERPRINT,
        test_name="test_add_task_rejects_empty_content",
        files_changed=["code/src/tasks.py"],
    )

    assert ranked[0].memory_id == same_test.memory_id


def test_lexical_match_surfaces_a_near_miss_failure():
    """A different fingerprint and a different test, but a recognisably similar failure."""
    near_miss = make_record(
        fingerprint=OTHER_FINGERPRINT,
        signature="assert <N> == <N> in add task validation",
        test_name="test_something_else",
        files_changed=("code/src/unrelated.py",),
    )
    unrelated = make_record(
        fingerprint="cccccccccccc",
        signature="ConnectionRefusedError while reaching the payment gateway",
        test_name="test_payments",
        files_changed=("code/src/payments.py",),
    )

    ranked = rank_records(
        [unrelated, near_miss],
        fingerprint=TARGET_FINGERPRINT,
        signature="assert <N> == <N> in add task validation",
    )

    assert ranked
    assert ranked[0].memory_id == near_miss.memory_id
    assert unrelated.memory_id not in [record.memory_id for record in ranked]


def test_repeated_observations_break_ties():
    once = make_record(files_changed=("code/src/a.py",), occurrences=1)
    thrice = make_record(files_changed=("code/src/b.py",), occurrences=3)

    ranked = rank_records([once, thrice], fingerprint=TARGET_FINGERPRINT)

    assert ranked[0].memory_id == thrice.memory_id


def test_records_matching_nothing_are_not_returned():
    unrelated = make_record(
        fingerprint=OTHER_FINGERPRINT,
        signature="ConnectionRefusedError while reaching the payment gateway",
        test_name="test_payments",
        files_changed=("code/src/payments.py",),
    )

    ranked = rank_records([unrelated], fingerprint=TARGET_FINGERPRINT, signature="assert <N> == <N>")

    assert ranked == []


# --- mode filtering ---------------------------------------------------------------


def test_mode_off_retrieves_nothing():
    records = [make_record(resolved=True), make_record(files_changed=("code/src/b.py",))]

    assert select_records(records, depth=12, fingerprint=TARGET_FINGERPRINT, mode=MemoryMode.OFF) == []


@pytest.mark.parametrize(
    "mode,expected_statuses",
    [
        (MemoryMode.VERIFIED, [Status.VERIFIED.value]),
        (MemoryMode.REFUTED, [Status.REFUTED.value]),
        (MemoryMode.ALL, [Status.VERIFIED.value, Status.REFUTED.value]),
    ],
)
def test_mode_filters_by_status(mode, expected_statuses):
    records = [make_record(files_changed=("code/src/a.py",), resolved=False), make_record(resolved=True)]

    selected = select_records(records, depth=12, fingerprint=TARGET_FINGERPRINT, mode=mode)

    assert sorted({record.status for record in selected}) == sorted(expected_statuses)
