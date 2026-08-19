"""Tests for consolidating a functionality's fix journal into lessons once its tests pass."""

import os
from unittest.mock import MagicMock

import pytest

from conformance_test_journal import LOOP_CONFORMANCE, VERDICT_IMPLEMENTATION_CODE, ConformanceTestJournal
from memory_management import PROJECT_LESSONS_FILE_NAME, MemoryManager
from render_machine.render_types import ConformanceTestsRunningContext

LESSONS_FILE_NAME = "conformance_test_lessons.json"


@pytest.fixture
def render_context(tmp_path):
    context = MagicMock()
    context.memory_manager.memory_folder = str(tmp_path)
    context.module_name = "module"
    context.frid_context.frid = "1"
    context.conformance_tests_running_context = ConformanceTestsRunningContext(
        current_testing_module_name="module",
        current_testing_frid="1",
        fix_attempts=0,
        conformance_tests_json={"1": {"folder_name": "tests_for_1"}},
        conformance_tests_render_attempts=0,
        current_testing_frid_specifications=None,
        should_prepare_testing_environment=False,
    )
    context.conformance_tests.fetch_existing_conformance_test_files.return_value = ([], {})
    context.codeplain_api.consolidate_conformance_test_lessons.return_value = {
        LESSONS_FILE_NAME: '{"lessons": [{"lesson": "a rule"}]}'
    }
    return context


@pytest.fixture
def memory_manager(render_context, tmp_path):
    return MemoryManager(render_context.codeplain_api, str(tmp_path), str(tmp_path))


def _write_journal(tmp_path, rounds=2):
    journal = ConformanceTestJournal("module", "1")
    for index in range(rounds):
        note = journal.record_failure(
            loop=LOOP_CONFORMANCE, exit_code=1, exact_signature=f"sig-{index}", evidence="E assert 3 == 4"
        )
        journal.record_attempt(
            LOOP_CONFORMANCE,
            VERDICT_IMPLEMENTATION_CODE,
            {f"file_{index}.py": "--- a\n+++ b\n@@ -1 +1 @@\n-old\n+new\n"},
            prompted_by=note,
        )
    journal.save(str(tmp_path))
    return journal


def test_lessons_are_consolidated_from_the_journal(memory_manager, render_context, tmp_path):
    _write_journal(tmp_path)

    memory_manager.consolidate_lessons(render_context)

    render_context.codeplain_api.consolidate_conformance_test_lessons.assert_called_once()
    journal_argument = render_context.codeplain_api.consolidate_conformance_test_lessons.call_args[0][-1]
    assert "Attempt 1" in journal_argument and "Attempt 2" in journal_argument


def test_the_consolidated_lessons_are_stored_where_memory_files_are_read_from(memory_manager, render_context, tmp_path):
    _write_journal(tmp_path)

    memory_manager.consolidate_lessons(render_context)

    _, memory_files_content = MemoryManager.fetch_memory_files(str(tmp_path))
    assert LESSONS_FILE_NAME in memory_files_content


def test_the_journal_is_discarded_once_its_lessons_have_been_taken(memory_manager, render_context, tmp_path):
    _write_journal(tmp_path)

    memory_manager.consolidate_lessons(render_context)

    assert not os.path.exists(ConformanceTestJournal.journal_path(str(tmp_path), "module", "1"))


def test_the_lessons_already_on_record_are_sent_so_they_can_be_reconciled(memory_manager, render_context, tmp_path):
    _write_journal(tmp_path)
    with open(os.path.join(str(tmp_path), LESSONS_FILE_NAME), "w", encoding="utf-8") as recorded:
        recorded.write('{"lessons": [{"lesson": "an earlier rule"}]}')

    memory_manager.consolidate_lessons(render_context)

    sent_memory_files = render_context.codeplain_api.consolidate_conformance_test_lessons.call_args[0][3]
    assert "an earlier rule" in sent_memory_files[LESSONS_FILE_NAME]


def test_a_functionality_that_needed_no_fixing_costs_no_call(memory_manager, render_context):
    """An empty journal means the tests passed first time, so there is nothing to have learned."""
    memory_manager.consolidate_lessons(render_context)

    render_context.codeplain_api.consolidate_conformance_test_lessons.assert_not_called()


def test_nothing_is_consolidated_when_no_functionality_is_under_test(memory_manager, render_context, tmp_path):
    _write_journal(tmp_path)
    render_context.conformance_tests_running_context.current_testing_frid = None

    memory_manager.consolidate_lessons(render_context)

    render_context.codeplain_api.consolidate_conformance_test_lessons.assert_not_called()


def test_the_journal_survives_a_consolidation_that_returned_nothing(memory_manager, render_context, tmp_path):
    """Returning no lessons is a valid outcome; it must not be confused with a failure to run."""
    _write_journal(tmp_path)
    render_context.codeplain_api.consolidate_conformance_test_lessons.return_value = {}

    memory_manager.consolidate_lessons(render_context)

    _, memory_files_content = MemoryManager.fetch_memory_files(str(tmp_path))
    assert memory_files_content == {}


def test_only_the_journal_of_the_functionality_under_test_is_harvested(memory_manager, render_context, tmp_path):
    """Journals are per functionality, so another functionality's record is not swept up by this one."""
    _write_journal(tmp_path)
    render_context.conformance_tests_running_context.current_testing_frid = "2"

    memory_manager.consolidate_lessons(render_context)

    render_context.codeplain_api.consolidate_conformance_test_lessons.assert_not_called()
    assert os.path.exists(ConformanceTestJournal.journal_path(str(tmp_path), "module", "1"))
