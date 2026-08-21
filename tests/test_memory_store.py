"""Tests for the evidential memory store.

Core invariants: nothing is ever deleted, repeat observations are counted rather than
duplicated, and a later green run promotes an earlier refuted record.
"""

import os

import pytest

from memory_management.record import Failure, Intervention, InterventionTarget, Scope, Status, Suite, Transition
from memory_management.retrieval import MemoryMode
from memory_management.store import MemoryStore


@pytest.fixture
def store(tmp_path):
    return MemoryStore(str(tmp_path / ".memory"))


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


def make_failure(fingerprint="a3f19c02b1d4", cause="assert 500 == 400"):
    return Failure(fingerprint=fingerprint, causes=[cause], exit_code=1)


def make_intervention(**overrides):
    defaults = dict(
        attempt_index=1,
        target=InterventionTarget.IMPLEMENTATION.value,
        files_changed=["code/src/tasks.py"],
        lines_changed=7,
        touched_implementation=True,
        touched_test_files=False,
    )
    defaults.update(overrides)
    return Intervention(**defaults)


def observe(store, exit_code_after, fingerprint_after, **overrides):
    return store.record_observation(
        scope=overrides.get("scope", make_scope()),
        failure=overrides.get("failure", make_failure()),
        intervention=overrides.get("intervention", make_intervention()),
        exit_code_after=exit_code_after,
        fingerprint_after=fingerprint_after,
        render_id="render-1",
    )


# --- writing ----------------------------------------------------------------------


def test_recording_persists_a_readable_record(store):
    record = observe(store, exit_code_after=1, fingerprint_after="a3f19c02b1d4")

    assert os.path.exists(os.path.join(store.memory_folder, record.file_name))
    assert [loaded.memory_id for loaded in store.load_all()] == [record.memory_id]


def test_refuted_and_verified_observations_both_persist(store):
    observe(store, exit_code_after=1, fingerprint_after="a3f19c02b1d4")
    observe(
        store,
        exit_code_after=0,
        fingerprint_after=None,
        intervention=make_intervention(files_changed=["code/src/validation.py"], lines_changed=3),
    )

    statuses = sorted(record.status for record in store.load_all())
    assert statuses == [Status.REFUTED.value, Status.VERIFIED.value]


def test_repeat_observation_of_same_attempt_is_counted_not_duplicated(store):
    observe(store, exit_code_after=1, fingerprint_after="a3f19c02b1d4")
    observe(store, exit_code_after=1, fingerprint_after="a3f19c02b1d4")
    observe(store, exit_code_after=1, fingerprint_after="a3f19c02b1d4")

    records = store.load_all()
    assert len(records) == 1
    assert records[0].occurrences == 3


def test_later_green_run_promotes_a_refuted_record(store):
    refuted = observe(store, exit_code_after=1, fingerprint_after="a3f19c02b1d4")
    assert refuted.status == Status.REFUTED.value

    promoted = observe(store, exit_code_after=0, fingerprint_after=None)

    assert promoted.status == Status.VERIFIED.value
    assert promoted.outcome.transition == Transition.RESOLVED.value
    assert promoted.occurrences == 2
    assert len(store.load_all()) == 1


def test_verified_record_is_not_demoted_by_a_later_failure(store):
    observe(store, exit_code_after=0, fingerprint_after=None)
    still_verified = observe(store, exit_code_after=1, fingerprint_after="a3f19c02b1d4")

    assert still_verified.status == Status.VERIFIED.value
    assert still_verified.occurrences == 2


def test_different_interventions_against_same_failure_are_separate_records(store):
    observe(store, exit_code_after=1, fingerprint_after="a3f19c02b1d4")
    observe(
        store,
        exit_code_after=1,
        fingerprint_after="a3f19c02b1d4",
        intervention=make_intervention(files_changed=["code/src/other.py"]),
    )

    assert len(store.load_all()) == 2


# --- lifecycle --------------------------------------------------------------------


def test_clear_removes_every_record(store):
    observe(store, exit_code_after=1, fingerprint_after="a3f19c02b1d4")
    store.clear()

    assert store.load_all() == []


def test_clear_on_a_missing_folder_is_a_no_op(store):
    store.clear()
    assert store.load_all() == []


def test_unreadable_files_are_skipped_not_fatal(store):
    observe(store, exit_code_after=1, fingerprint_after="a3f19c02b1d4")
    with open(os.path.join(store.memory_folder, "garbage.json"), "w") as broken:
        broken.write("{ not json")
    with open(os.path.join(store.memory_folder, "notes.txt"), "w") as ignored:
        ignored.write("ignored")

    assert len(store.load_all()) == 1


# --- retrieval integration --------------------------------------------------------


def test_retrieve_returns_payload_shaped_dict(store):
    record = observe(store, exit_code_after=0, fingerprint_after=None)

    retrieved = store.retrieve(fingerprint="a3f19c02b1d4", fix_attempts=3)

    assert list(retrieved) == [record.file_name]
    assert '"status": "VERIFIED"' in retrieved[record.file_name]


def test_retrieve_returns_nothing_on_the_first_pass(store):
    observe(store, exit_code_after=0, fingerprint_after=None)

    assert store.retrieve(fingerprint="a3f19c02b1d4", fix_attempts=0) == {}


def test_retrieve_respects_memory_mode_off(tmp_path):
    store = MemoryStore(str(tmp_path / ".memory"), memory_mode=MemoryMode.OFF)
    observe(store, exit_code_after=0, fingerprint_after=None)

    assert store.retrieve(fingerprint="a3f19c02b1d4", fix_attempts=5) == {}


def test_retrieve_respects_memory_mode_verified_only(tmp_path):
    store = MemoryStore(str(tmp_path / ".memory"), memory_mode=MemoryMode.VERIFIED)
    observe(store, exit_code_after=1, fingerprint_after="a3f19c02b1d4")

    assert store.retrieve(fingerprint="a3f19c02b1d4", fix_attempts=5) == {}


# --- suite separation -------------------------------------------------------------


def test_conformance_and_unittest_records_coexist_and_are_distinguishable(store):
    observe(store, exit_code_after=1, fingerprint_after="a3f19c02b1d4")
    observe(
        store,
        exit_code_after=1,
        fingerprint_after="a3f19c02b1d4",
        scope=make_scope(suite=Suite.UNITTEST.value, test_name=None),
        intervention=make_intervention(target=InterventionTarget.UNCLASSIFIED.value),
    )

    records = store.load_all()

    assert len(records) == 2
    assert sorted(record.scope.suite for record in records) == [Suite.CONFORMANCE.value, Suite.UNITTEST.value]
    # The suite is visible in the file name too, so the two are obvious on disk.
    assert sorted(record.file_name.split("-")[0] for record in records) == ["conformance", "unittest"]


def test_retrieval_prefers_the_suite_being_fixed(store):
    observe(store, exit_code_after=0, fingerprint_after=None)
    observe(
        store,
        exit_code_after=0,
        fingerprint_after=None,
        scope=make_scope(suite=Suite.UNITTEST.value, test_name=None),
        intervention=make_intervention(target=InterventionTarget.UNCLASSIFIED.value),
    )

    retrieved = store.retrieve(fingerprint="a3f19c02b1d4", fix_attempts=5, suite=Suite.UNITTEST.value)

    assert list(retrieved)[0].startswith("unittest-")


def test_unit_test_records_leave_the_file_split_undetermined(store):
    record = observe(
        store,
        exit_code_after=1,
        fingerprint_after="a3f19c02b1d4",
        scope=make_scope(suite=Suite.UNITTEST.value, test_name=None),
        intervention=Intervention(
            attempt_index=2,
            target=InterventionTarget.UNCLASSIFIED.value,
            files_changed=["code/src/tasks.py"],
            lines_changed=4,
        ),
    )

    # None means "not determined", which is not the same as a determined False.
    assert record.intervention.touched_implementation is None
    assert record.intervention.touched_test_files is None
    assert record.flags == []
