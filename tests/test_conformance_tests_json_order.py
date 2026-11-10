"""The entries of conformance_tests.json follow the order of the functionalities in the specification.

A regeneration removes the entry of the functionality and writes it again, so without this the entry
moves to the end. A module that requires this one runs these tests in the order of the entries.
"""

from render_machine.actions.render_conformance_tests import RenderConformanceTests

FRIDS = ["run-cli", "add-command", "list-command", "report-command"]


def test_a_rewritten_entry_goes_back_to_its_position():
    conformance_tests_json = {"run-cli": 1, "list-command": 3, "report-command": 4, "add-command": 2}

    RenderConformanceTests._keep_specification_order(conformance_tests_json, FRIDS)

    assert list(conformance_tests_json) == FRIDS


def test_the_same_object_is_reordered():
    """The running context holds this dict, so it must be reordered in place."""
    conformance_tests_json = {"list-command": 3, "run-cli": 1}
    same_object = conformance_tests_json

    RenderConformanceTests._keep_specification_order(conformance_tests_json, FRIDS)

    assert same_object is conformance_tests_json
    assert list(same_object) == ["run-cli", "list-command"]
    assert same_object["list-command"] == 3


def test_an_entry_the_specification_no_longer_has_goes_last():
    conformance_tests_json = {"removed-command": 9, "add-command": 2, "run-cli": 1}

    RenderConformanceTests._keep_specification_order(conformance_tests_json, FRIDS)

    assert list(conformance_tests_json) == ["run-cli", "add-command", "removed-command"]
