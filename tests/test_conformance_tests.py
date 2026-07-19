import json
import os
import tempfile

import pytest

from render_machine.conformance_tests import CONFORMANCE_TESTS_DEFINITION_FILE_NAME, ConformanceTests

MODULE_NAME = "my_module"


@pytest.fixture
def conformance_tests_dir():
    with tempfile.TemporaryDirectory() as d:
        yield d


@pytest.fixture
def conformance_tests(conformance_tests_dir):
    return ConformanceTests(conformance_tests_dir, CONFORMANCE_TESTS_DEFINITION_FILE_NAME)


def _write_file(base_dir, relative_path, content):
    full_path = os.path.join(base_dir, relative_path)
    os.makedirs(os.path.dirname(full_path), exist_ok=True)
    with open(full_path, "w") as f:
        f.write(content)


def test_fetch_all_existing_conformance_test_files_empty_when_module_folder_missing(conformance_tests):
    assert conformance_tests.fetch_all_existing_conformance_test_files(MODULE_NAME) == {}


def test_fetch_all_existing_conformance_test_files_multiple_folders(conformance_tests, conformance_tests_dir):
    module_folder = os.path.join(conformance_tests_dir, MODULE_NAME)
    _write_file(module_folder, os.path.join("first_functionality", "test_first.py"), "first test")
    _write_file(module_folder, os.path.join("second_functionality", "test_second.py"), "second test")
    _write_file(module_folder, os.path.join("second_functionality", "helpers", "util.py"), "helper")

    files_content = conformance_tests.fetch_all_existing_conformance_test_files(MODULE_NAME)

    assert files_content == {
        os.path.join("first_functionality", "test_first.py"): "first test",
        os.path.join("second_functionality", "test_second.py"): "second test",
        os.path.join("second_functionality", "helpers", "util.py"): "helper",
    }


def test_fetch_all_existing_conformance_test_files_excludes_hidden_folders(conformance_tests, conformance_tests_dir):
    module_folder = os.path.join(conformance_tests_dir, MODULE_NAME)
    _write_file(module_folder, os.path.join("own_functionality", "test_own.py"), "own test")
    _write_file(module_folder, os.path.join(".required_module", "some_frid", "test_required.py"), "required test")

    files_content = conformance_tests.fetch_all_existing_conformance_test_files(MODULE_NAME)

    assert files_content == {os.path.join("own_functionality", "test_own.py"): "own test"}


def test_fetch_all_existing_conformance_test_files_excludes_definition_file(conformance_tests, conformance_tests_dir):
    module_folder = os.path.join(conformance_tests_dir, MODULE_NAME)
    _write_file(module_folder, os.path.join("some_functionality", "test_some.py"), "some test")
    _write_file(module_folder, CONFORMANCE_TESTS_DEFINITION_FILE_NAME, json.dumps({"frid": {}}))

    files_content = conformance_tests.fetch_all_existing_conformance_test_files(MODULE_NAME)

    assert files_content == {os.path.join("some_functionality", "test_some.py"): "some test"}


def test_fetch_all_existing_conformance_test_files_skips_binary_files(conformance_tests, conformance_tests_dir):
    module_folder = os.path.join(conformance_tests_dir, MODULE_NAME)
    _write_file(module_folder, os.path.join("some_functionality", "test_some.py"), "some test")
    binary_file_path = os.path.join(module_folder, "some_functionality", "fixture.bin")
    with open(binary_file_path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n\x00\x00\xff\xfe\xfd")

    files_content = conformance_tests.fetch_all_existing_conformance_test_files(MODULE_NAME)

    assert files_content == {os.path.join("some_functionality", "test_some.py"): "some test"}


class _FakeModule:
    def __init__(self, module_name):
        self.module_name = module_name


def test_get_module_suite_run_folder_own_module(conformance_tests, conformance_tests_dir):
    folder = conformance_tests.get_module_suite_run_folder(MODULE_NAME, [], MODULE_NAME)

    assert folder == os.path.join(conformance_tests_dir, MODULE_NAME)


def test_get_module_suite_run_folder_required_module_with_copy(conformance_tests, conformance_tests_dir):
    copy_folder = os.path.join(conformance_tests_dir, MODULE_NAME, ".required_module")
    _write_file(copy_folder, os.path.join("some_frid", "test_x.py"), "copied test")

    folder = conformance_tests.get_module_suite_run_folder(
        MODULE_NAME, [_FakeModule("required_module")], "required_module"
    )

    assert folder == copy_folder


def test_get_module_suite_run_folder_required_module_without_copy(conformance_tests, conformance_tests_dir):
    folder = conformance_tests.get_module_suite_run_folder(
        MODULE_NAME, [_FakeModule("required_module")], "required_module"
    )

    assert folder == os.path.join(conformance_tests_dir, "required_module")


def test_fetch_all_existing_conformance_test_files_includes_shared_root_files(conformance_tests, conformance_tests_dir):
    module_folder = os.path.join(conformance_tests_dir, MODULE_NAME)
    _write_file(module_folder, os.path.join("some_functionality", "test_some.py"), "some test")
    _write_file(module_folder, "shared_helpers.py", "shared helper")
    _write_file(module_folder, CONFORMANCE_TESTS_DEFINITION_FILE_NAME, json.dumps({}))

    files_content = conformance_tests.fetch_all_existing_conformance_test_files(MODULE_NAME)

    assert files_content == {
        os.path.join("some_functionality", "test_some.py"): "some test",
        "shared_helpers.py": "shared helper",
    }


def test_find_response_file_violations_allows_own_subfolder_and_new_root_files(
    conformance_tests, conformance_tests_dir
):
    module_folder = os.path.join(conformance_tests_dir, MODULE_NAME)
    _write_file(module_folder, os.path.join("earlier_suite", "test_earlier.py"), "earlier")

    violations = conformance_tests.find_response_file_violations(
        MODULE_NAME,
        "current_suite",
        {
            os.path.join("current_suite", "test_new.py"): "new test",
            "shared_helpers.py": "brand new helper",
            os.path.join("support", "util.py"): "new shared dir file",
        },
    )

    assert violations == []


def test_find_response_file_violations_rejects_other_suites_and_hidden_copies(conformance_tests, conformance_tests_dir):
    module_folder = os.path.join(conformance_tests_dir, MODULE_NAME)
    _write_file(module_folder, os.path.join("earlier_suite", "test_earlier.py"), "earlier")

    violations = conformance_tests.find_response_file_violations(
        MODULE_NAME,
        "current_suite",
        {
            os.path.join("earlier_suite", "test_earlier.py"): "rewritten",
            os.path.join(".required_module", "suite", "test_x.py"): "copied",
            CONFORMANCE_TESTS_DEFINITION_FILE_NAME: "{}",
        },
    )

    assert len(violations) == 3


def test_find_response_file_violations_shared_root_file_insertion_only(conformance_tests, conformance_tests_dir):
    module_folder = os.path.join(conformance_tests_dir, MODULE_NAME)
    _write_file(module_folder, "shared_helpers.py", "line one\nline two\n")

    extended = "line one\nnew line in between\nline two\nnew line at end\n"
    assert (
        conformance_tests.find_response_file_violations(MODULE_NAME, "current_suite", {"shared_helpers.py": extended})
        == []
    )

    modified = "line one CHANGED\nline two\n"
    violations = conformance_tests.find_response_file_violations(
        MODULE_NAME, "current_suite", {"shared_helpers.py": modified}
    )
    assert len(violations) == 1
    assert "adding lines" in violations[0]

    truncated = "line one\n"
    violations = conformance_tests.find_response_file_violations(
        MODULE_NAME, "current_suite", {"shared_helpers.py": truncated}
    )
    assert len(violations) == 1
