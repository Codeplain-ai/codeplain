"""Tests for the query the render context sends to the memory store.

The loop identity a fix reads with has to be the same identity the following test run
writes with, otherwise the history is stored under a key nobody queries. These tests pin
both halves against each other.
"""

from types import SimpleNamespace

import pytest

from memory_management import Suite
from render_machine.render_context import RenderContext

FAILURE_OUTPUT = "AssertionError: expected 3 tasks, found 2\n  at tasks_test.py:41"


class RecordingStore:
    """Captures the query instead of answering it."""

    def __init__(self):
        self.calls: list[dict] = []

    def retrieve(self, **query):
        self.calls.append(query)
        return {}


def make_context(store, fix_attempts=2, conformance_tests_exist=True):
    context = object.__new__(RenderContext)
    context.memory_store = store
    context.module_name = "backend"
    context.frid_context = SimpleNamespace(frid="2.3")
    context.conformance_tests_running_context = SimpleNamespace(
        current_testing_module_name="frontend",
        current_testing_frid="1.4",
        current_conformance_tests_exist=lambda: conformance_tests_exist,
        get_current_conformance_test_folder_name=lambda: "test_add_task",
        fix_attempts=fix_attempts,
    )
    context.unit_tests_running_context = SimpleNamespace(
        changed_files={"code/src/tasks.py"},
        fix_attempts=fix_attempts,
    )
    return context


def test_the_conformance_query_identifies_the_functionality_under_test():
    """Matches the scope ``RunConformanceTests`` records: the testing module and frid."""
    store = RecordingStore()
    make_context(store).retrieve_memory_for_conformance_failure(FAILURE_OUTPUT)

    query = store.calls[0]
    assert query["testing_module"] == "frontend"
    assert query["testing_frid"] == "1.4"
    assert query["suite"] == Suite.CONFORMANCE.value


def test_the_unittest_query_identifies_the_functionality_being_implemented():
    """Matches the scope ``RunUnitTests`` records: the rendering module and its frid."""
    store = RecordingStore()
    make_context(store).retrieve_memory_for_unittest_failure(FAILURE_OUTPUT)

    query = store.calls[0]
    assert query["testing_module"] == "backend"
    assert query["testing_frid"] == "2.3"
    assert query["suite"] == Suite.UNITTEST.value


def test_the_failure_is_fingerprinted_before_it_is_queried_with():
    store = RecordingStore()
    make_context(store).retrieve_memory_for_conformance_failure(FAILURE_OUTPUT)

    query = store.calls[0]
    assert query["fingerprint"]
    assert query["signature"]


def test_the_attempt_count_travels_with_the_query():
    store = RecordingStore()
    make_context(store, fix_attempts=7).retrieve_memory_for_conformance_failure(FAILURE_OUTPUT)

    assert store.calls[0]["fix_attempts"] == 7


def test_loop_identity_survives_a_missing_test_folder():
    """No conformance test folder yet still leaves the loop addressable by functionality."""
    store = RecordingStore()
    make_context(store, conformance_tests_exist=False).retrieve_memory_for_conformance_failure(FAILURE_OUTPUT)

    query = store.calls[0]
    assert query["test_name"] is None
    assert query["testing_frid"] == "1.4"


@pytest.mark.parametrize(
    "retrieve",
    ["retrieve_memory_for_conformance_failure", "retrieve_memory_for_unittest_failure"],
)
def test_no_failure_means_no_query(retrieve):
    store = RecordingStore()
    assert getattr(make_context(store), retrieve)(None) == {}
    assert store.calls == []
