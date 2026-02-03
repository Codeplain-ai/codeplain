import pytest

import plain_file
import plain_spec


def test_get_linked_resources_invalid_input():
    with pytest.raises(ValueError, match="plain_source_tree must be a dictionary."):
        plain_spec.collect_linked_resources([], [], None, True)


def test_get_frids_simple(get_test_data_path):
    _, plain_source, _ = plain_file.plain_file_parser("simple.plain", [get_test_data_path("data/")])

    frids = list(plain_spec.get_frids(plain_source))

    assert frids == ["1"]


def test_get_first_functional_requirement_simple(get_test_data_path):
    _, plain_source, _ = plain_file.plain_file_parser("simple.plain", [get_test_data_path("data/")])

    assert plain_spec.get_first_frid(plain_source) == "1"


def test_get_next_frid_not_exists(get_test_data_path):
    _, plain_source, _ = plain_file.plain_file_parser("simple.plain", [get_test_data_path("data/")])

    with pytest.raises(Exception, match="Functional requirement 2 does not exist."):
        plain_spec.get_next_frid(plain_source, "2")


def test_get_next_frid_simple(get_test_data_path):
    _, plain_source, _ = plain_file.plain_file_parser("simple.plain", [get_test_data_path("data/")])

    assert plain_spec.get_next_frid(plain_source, "1") is None


def test_get_previous_frid_not_exists(get_test_data_path):
    _, plain_source, _ = plain_file.plain_file_parser("simple.plain", [get_test_data_path("data/")])

    with pytest.raises(Exception, match="Functional requirement 2 does not exist."):
        plain_spec.get_previous_frid(plain_source, "2")


def test_get_previous_frid_no_previous_frid(get_test_data_path):
    _, plain_source, _ = plain_file.plain_file_parser("simple.plain", [get_test_data_path("data/")])

    assert plain_spec.get_previous_frid(plain_source, "1") is None


def test_get_specifications_simple(get_test_data_path):
    _, plain_source, _ = plain_file.plain_file_parser("simple.plain", [get_test_data_path("data/")])

    with pytest.raises(Exception, match="Functional requirement a does not exist."):
        plain_spec.get_specifications_for_frid(plain_source, "a")

    frid = plain_spec.get_first_frid(plain_source)

    specifications, _ = plain_spec.get_specifications_for_frid(plain_source, frid)

    assert specifications == {
        "definitions": [],
        "implementation reqs": ["- Simple non-functional requirement"],
        "test reqs": [],
        "functional specs": ["- Simple functional requirement"],
    }
