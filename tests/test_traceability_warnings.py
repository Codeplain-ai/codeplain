"""The client warns about a scenario with no conformance test, a test that implements no scenario,
and a scenario with more than one test. Both test renders report the same traceability, so the
conformance call and the acceptance call are checked the same way."""

from types import SimpleNamespace
from unittest.mock import patch

import file_utils  # noqa: F401  # imported before plain_modules, which it imports back from
from render_machine.actions.render_conformance_tests import RenderConformanceTests


def _render_context(frid: str = "add-task"):
    return SimpleNamespace(
        conformance_tests_running_context=SimpleNamespace(current_testing_frid=frid),
    )


def _warn(traceability: dict) -> list[str]:
    with patch("render_machine.actions.render_conformance_tests.console") as console:
        RenderConformanceTests._warn_about_untraced_scenarios(_render_context(), traceability)
    return [call.args[0] for call in console.warning.call_args_list]


def test_every_scenario_implemented_warns_about_nothing():
    assert _warn({"implemented": {"add-first-task": ["test_add_first_task"]}, "missing": [], "extra": []}) == []


def test_a_scenario_without_a_test_is_named():
    [warning] = _warn({"implemented": {}, "missing": ["add-second-task"], "extra": []})

    assert "add-task" in warning
    assert "add-second-task" in warning


def test_a_test_implementing_no_scenario_is_named():
    [warning] = _warn({"implemented": {}, "missing": [], "extra": ["test_something_else"]})

    assert "test_something_else" in warning


def test_a_scenario_with_more_than_one_test_is_named():
    [warning] = _warn(
        {
            "implemented": {"add-first-task": ["test_add_first_task", "test_add_first_task_scenario"]},
            "missing": [],
            "extra": [],
            "duplicated": ["add-first-task"],
        }
    )

    assert "add-task" in warning
    assert "add-first-task" in warning


def test_every_fault_is_reported():
    warnings = _warn(
        {
            "implemented": {},
            "missing": ["add-second-task"],
            "extra": ["test_something_else"],
            "duplicated": ["add-first-task"],
        }
    )

    assert len(warnings) == 3


def test_a_response_without_the_lists_warns_about_nothing():
    assert _warn({}) == []
