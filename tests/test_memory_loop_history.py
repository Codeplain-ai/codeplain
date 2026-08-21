"""Tests for loop-history retrieval.

The property under test is that a fixer can always see what has already been tried
against the functionality it is fixing. That has to hold even when a fix turns one failure
into a different one, which is exactly the case a fingerprint match misses - and the case
that produces thrashing between two partial fixes.
"""

import pytest

from memory_management.record import (
    RECORD_KIND_FIX_LOOP_SUMMARY,
    Failure,
    Intervention,
    InterventionTarget,
    Scope,
    Suite,
    build_record,
)
from memory_management.retrieval import (
    MAX_LOOP_HISTORY,
    STATE_RESOLVED,
    MemoryMode,
    failure_state_sequence,
    is_loop_history,
    revisited_failure_states,
    select_memory,
)
from memory_management.store import MEMORY_BLOCK_FILE_NAME, MemoryStore

TESTING_MODULE = "backend"
TESTING_FRID = "2.1"
SUITE = Suite.CONFORMANCE.value

STATE_A = "aaaaaaaaaaaa"
STATE_B = "bbbbbbbbbbbb"
STATE_C = "cccccccccccc"


def make_attempt(
    attempt_index,
    fingerprint_before,
    fingerprint_after,
    files_changed=("code/src/tasks.py",),
    testing_module=TESTING_MODULE,
    testing_frid=TESTING_FRID,
    suite=SUITE,
    resolved=False,
    lines_changed=5,
):
    """One recorded attempt in a fix loop, at a stated position in the chain."""
    return build_record(
        scope=Scope(
            module="backend",
            frid="2.3",
            testing_module=testing_module,
            testing_frid=testing_frid,
            suite=suite,
            test_name="test_add_task_rejects_empty_content",
        ),
        failure=Failure(fingerprint=fingerprint_before, causes=[f"assert failure {fingerprint_before}"], exit_code=1),
        intervention=Intervention(
            attempt_index=attempt_index,
            target=InterventionTarget.IMPLEMENTATION.value,
            files_changed=list(files_changed),
            lines_changed=lines_changed,
            touched_implementation=True,
        ),
        exit_code_after=0 if resolved else 1,
        fingerprint_after=None if resolved else fingerprint_after,
        observed_at=f"2026-08-20T10:{attempt_index:02d}:00Z",
    )


# --- loop identity ----------------------------------------------------------------


def test_an_attempt_that_changed_the_failure_is_still_loop_history():
    """The case a fingerprint match misses: the failure moved, the loop did not."""
    record = make_attempt(1, STATE_A, STATE_B)

    assert is_loop_history(record, TESTING_MODULE, TESTING_FRID, SUITE)


def test_a_different_functionality_is_not_loop_history():
    record = make_attempt(1, STATE_A, STATE_B, testing_frid="9.9")

    assert not is_loop_history(record, TESTING_MODULE, TESTING_FRID, SUITE)


def test_the_other_test_surface_is_not_the_same_loop():
    """Unit-test and conformance fixing of one functionality are two separate searches."""
    record = make_attempt(1, STATE_A, STATE_B, suite=Suite.UNITTEST.value)

    assert not is_loop_history(record, TESTING_MODULE, TESTING_FRID, SUITE)


@pytest.mark.parametrize("module,frid", [(None, TESTING_FRID), (TESTING_MODULE, None), (None, None)])
def test_an_unidentified_loop_matches_nothing(module, frid):
    """Without a loop identity there is no loop history - not a match against everything."""
    record = make_attempt(1, STATE_A, STATE_B)

    assert not is_loop_history(record, module, frid, SUITE)


# --- the thrashing case -----------------------------------------------------------


def test_previous_attempts_survive_a_changed_failure():
    """Retrieval keys on the functionality, so history is not lost when the failure moves."""
    history = [make_attempt(1, STATE_A, STATE_B), make_attempt(2, STATE_B, STATE_A)]

    result = select_memory(
        history,
        testing_module=TESTING_MODULE,
        testing_frid=TESTING_FRID,
        suite=SUITE,
        # The failure currently on screen is a third state neither attempt was applied to.
        fingerprint=STATE_C,
        failure_text="assert failure cccccccccccc",
        fix_attempts=2,
    )

    assert [record.intervention.attempt_index for record in result.loop_history] == [1, 2]


def test_loop_history_is_not_cut_by_the_associative_depth():
    """Ten attempts are all returned at a depth that would otherwise allow three records."""
    history = [make_attempt(index, STATE_A, STATE_B, lines_changed=index) for index in range(1, 11)]

    result = select_memory(
        history,
        testing_module=TESTING_MODULE,
        testing_frid=TESTING_FRID,
        suite=SUITE,
        fingerprint=STATE_A,
        fix_attempts=1,
    )

    assert len(result.loop_history) == 10


def test_loop_history_is_ordered_oldest_first():
    history = [make_attempt(3, STATE_C, STATE_A), make_attempt(1, STATE_A, STATE_B), make_attempt(2, STATE_B, STATE_C)]

    result = select_memory(history, testing_module=TESTING_MODULE, testing_frid=TESTING_FRID, suite=SUITE)

    assert [record.intervention.attempt_index for record in result.loop_history] == [1, 2, 3]


def test_a_record_is_never_both_loop_history_and_associative_evidence():
    history = [make_attempt(1, STATE_A, STATE_B)]
    elsewhere = [make_attempt(1, STATE_A, STATE_B, testing_frid="9.9")]

    result = select_memory(
        history + elsewhere,
        testing_module=TESTING_MODULE,
        testing_frid=TESTING_FRID,
        suite=SUITE,
        fingerprint=STATE_A,
        fix_attempts=3,
    )

    assert len(result.loop_history) == 1
    assert len(result.associative) == 1
    assert result.loop_history[0] not in result.associative


def test_associative_evidence_still_arrives_alongside_loop_history():
    history = [make_attempt(1, STATE_A, STATE_B)]
    elsewhere = [make_attempt(1, STATE_A, STATE_C, testing_frid="9.9", files_changed=("code/src/other.py",))]

    result = select_memory(
        history + elsewhere,
        testing_module=TESTING_MODULE,
        testing_frid=TESTING_FRID,
        suite=SUITE,
        fingerprint=STATE_A,
        fix_attempts=3,
    )

    assert [record.scope.testing_frid for record in result.associative] == ["9.9"]


# --- the derived chain ------------------------------------------------------------


def test_the_chain_runs_from_the_first_failure_to_the_current_one():
    history = [make_attempt(1, STATE_A, STATE_B), make_attempt(2, STATE_B, STATE_C)]

    assert failure_state_sequence(history) == [STATE_A, STATE_B, STATE_C]


def test_a_resolved_loop_ends_in_resolved():
    history = [make_attempt(1, STATE_A, None, resolved=True)]

    assert failure_state_sequence(history) == [STATE_A, STATE_RESOLVED]


def test_an_empty_loop_has_no_chain():
    assert failure_state_sequence([]) == []


def test_a_returning_failure_is_reported_as_revisited():
    """A -> B -> A means the two changes cancelled each other out."""
    assert revisited_failure_states([STATE_A, STATE_B, STATE_A]) == [STATE_A]


def test_a_failure_that_never_moved_is_not_revisited():
    """A change with no effect is not the same finding as a loop undoing its own progress."""
    assert revisited_failure_states([STATE_A, STATE_A, STATE_A]) == []


def test_resolved_and_unknown_states_are_not_treated_as_revisits():
    assert revisited_failure_states([STATE_RESOLVED, STATE_A, STATE_RESOLVED]) == []
    assert revisited_failure_states(["unknown", STATE_A, "unknown"]) == []


def test_the_summary_reports_the_chain_and_the_file_tally():
    history = [
        make_attempt(1, STATE_A, STATE_B, files_changed=("code/src/tasks.py",)),
        make_attempt(2, STATE_B, STATE_A, files_changed=("code/src/tasks.py", "code/src/api.py")),
    ]

    result = select_memory(history, testing_module=TESTING_MODULE, testing_frid=TESTING_FRID, suite=SUITE)
    summary = result.loop_summary

    assert summary["kind"] == RECORD_KIND_FIX_LOOP_SUMMARY
    assert summary["attempts_recorded"] == 2
    assert summary["attempts_listed"] == 2
    assert summary["failure_state_sequence"] == [STATE_A, STATE_B, STATE_A]
    assert summary["revisited_failure_states"] == [STATE_A]
    assert summary["files_changed_across_attempts"]["code/src/tasks.py"] == 2
    assert summary["distinct_failure_states"] == 2


def test_there_is_no_summary_before_the_first_attempt():
    result = select_memory([], testing_module=TESTING_MODULE, testing_frid=TESTING_FRID, suite=SUITE)

    assert result.loop_summary is None


def test_a_pathological_loop_keeps_the_most_recent_attempts_and_says_so():
    """The backstop is not a silent truncation - the summary reports the full count."""
    history = [make_attempt(index, STATE_A, STATE_B, lines_changed=index) for index in range(1, MAX_LOOP_HISTORY + 6)]

    result = select_memory(history, testing_module=TESTING_MODULE, testing_frid=TESTING_FRID, suite=SUITE)

    assert len(result.loop_history) == MAX_LOOP_HISTORY
    assert result.loop_history[-1].intervention.attempt_index == MAX_LOOP_HISTORY + 5
    assert result.loop_summary["attempts_recorded"] == MAX_LOOP_HISTORY + 5
    assert result.loop_summary["attempts_listed"] == MAX_LOOP_HISTORY


# --- modes ------------------------------------------------------------------------


def test_loop_mode_drops_associative_evidence_but_keeps_the_history():
    history = [make_attempt(1, STATE_A, STATE_B)]
    elsewhere = [make_attempt(1, STATE_A, STATE_C, testing_frid="9.9")]

    result = select_memory(
        history + elsewhere,
        testing_module=TESTING_MODULE,
        testing_frid=TESTING_FRID,
        suite=SUITE,
        fingerprint=STATE_A,
        fix_attempts=3,
        mode=MemoryMode.LOOP,
    )

    assert len(result.loop_history) == 1
    assert result.associative == []


def test_off_mode_returns_nothing_at_all():
    result = select_memory(
        [make_attempt(1, STATE_A, STATE_B)],
        testing_module=TESTING_MODULE,
        testing_frid=TESTING_FRID,
        suite=SUITE,
        mode=MemoryMode.OFF,
    )

    assert result.loop_history == []
    assert result.associative == []
    assert result.loop_summary is None


# --- what reaches the payload -----------------------------------------------------


def _record_in(store, record):
    store.record_observation(
        scope=record.scope,
        failure=record.failure,
        intervention=record.intervention,
        exit_code_after=record.outcome.exit_code_after,
        fingerprint_after=record.outcome.fingerprint_after,
    )


def test_the_payload_is_one_rendered_block(tmp_path):
    """Memory is not cacheable in the prompt, so it travels rendered, not as documents."""
    store = MemoryStore(str(tmp_path / ".memory"))
    for record in [make_attempt(1, STATE_A, STATE_B), make_attempt(2, STATE_B, STATE_A)]:
        _record_in(store, record)

    memory_files = store.retrieve(
        testing_module=TESTING_MODULE,
        testing_frid=TESTING_FRID,
        suite=SUITE,
        fingerprint=STATE_C,
        fix_attempts=2,
    )

    assert list(memory_files) == [MEMORY_BLOCK_FILE_NAME]


def test_the_block_lists_every_attempt_in_order_with_its_outcome(tmp_path):
    store = MemoryStore(str(tmp_path / ".memory"))
    for record in [make_attempt(1, STATE_A, STATE_B), make_attempt(2, STATE_B, STATE_A)]:
        _record_in(store, record)

    block = store.retrieve(
        testing_module=TESTING_MODULE, testing_frid=TESTING_FRID, suite=SUITE, fingerprint=STATE_C, fix_attempts=2
    )[MEMORY_BLOCK_FILE_NAME]

    assert "Attempts already made against backend / functionality 2.1" in block
    assert block.index("| 1 |") < block.index("| 2 |")
    assert "A -> B" in block and "B -> A" in block


def test_the_block_names_each_failure_state_once(tmp_path):
    """The legend is the token saving: one description per state, not per record."""
    store = MemoryStore(str(tmp_path / ".memory"))
    for record in [make_attempt(1, STATE_A, STATE_A), make_attempt(2, STATE_A, STATE_A)]:
        _record_in(store, record)

    block = store.retrieve(
        testing_module=TESTING_MODULE, testing_frid=TESTING_FRID, suite=SUITE, fingerprint=STATE_A, fix_attempts=2
    )[MEMORY_BLOCK_FILE_NAME]

    assert block.count(f"assert failure {STATE_A}") == 1


def test_the_block_reports_an_observed_cycle(tmp_path):
    store = MemoryStore(str(tmp_path / ".memory"))
    for record in [make_attempt(1, STATE_A, STATE_B), make_attempt(2, STATE_B, STATE_A)]:
        _record_in(store, record)

    block = store.retrieve(
        testing_module=TESTING_MODULE, testing_frid=TESTING_FRID, suite=SUITE, fingerprint=STATE_A, fix_attempts=2
    )[MEMORY_BLOCK_FILE_NAME]

    assert "cancelled each other out" in block


def test_an_attempt_in_another_module_is_not_folded_into_this_loop(tmp_path):
    """Same change, same failure, different module - a shared build problem, not one record."""
    store = MemoryStore(str(tmp_path / ".memory"))
    _record_in(store, make_attempt(1, STATE_A, STATE_A))
    _record_in(store, make_attempt(1, STATE_A, STATE_A, testing_module="frontend"))

    assert len(store.load_all()) == 2


def test_nothing_retrieved_means_no_payload_at_all(tmp_path):
    store = MemoryStore(str(tmp_path / ".memory"))

    assert store.retrieve(testing_module=TESTING_MODULE, testing_frid=TESTING_FRID, suite=SUITE) == {}
