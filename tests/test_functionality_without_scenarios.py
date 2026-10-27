"""A functionality whose test plain file holds no scenario: it is rendered, without conformance tests.

A functionality with no test plain file at all is a different case and stops the run.
"""

import os

import pytest

import file_utils  # noqa: F401  # imported before plain_modules, which it imports back from
import plain2code
from plain_modules import PlainModule

MODULE = "required_with_acceptance_tests.plain"
EMPTY_TEST_PLAIN_FILE = """---
module: required_with_acceptance_tests
func_id: entry-point
---

***conformance tests***

***acceptance tests***
"""


@pytest.fixture
def project(tmp_path, get_test_data_path):
    """A copy of the fixture project, so a test can edit its test plain files."""
    import shutil

    source = get_test_data_path("data/acceptance_tests_warning")
    specs = tmp_path / "specs"
    shutil.copytree(source, specs)
    return specs


def _build(project, tmp_path, module=MODULE):
    return PlainModule(
        module,
        str(tmp_path / "build"),
        [str(project)],
        [str(project / "tests")],
        conformance_tests_enabled=True,
    )


def test_a_test_plain_file_with_a_scenario_is_not_reported(project, tmp_path):
    module = _build(project, tmp_path)

    assert module.functionalities_without_scenarios == []


def test_a_test_plain_file_without_scenarios_is_reported(project, tmp_path):
    (project / "tests" / "required_with_acceptance_tests" / "entry-point.plain").write_text(EMPTY_TEST_PLAIN_FILE)

    module = _build(project, tmp_path)

    assert module.functionalities_without_scenarios == [("required_with_acceptance_tests", "entry-point")]


def test_a_missing_test_plain_file_stops_the_run(project, tmp_path):
    os.remove(project / "tests" / "required_with_acceptance_tests" / "entry-point.plain")

    with pytest.raises(Exception, match="has no test plain file"):
        _build(project, tmp_path)


def test_the_warning_names_every_functionality_without_scenarios(project, tmp_path, monkeypatch):
    (project / "tests" / "required_with_acceptance_tests" / "entry-point.plain").write_text(EMPTY_TEST_PLAIN_FILE)
    module = _build(project, tmp_path)

    warnings = []
    monkeypatch.setattr(plain2code.console, "warning", lambda message: warnings.append(message))
    plain2code.warn_about_functionalities_without_scenarios(module)

    assert len(warnings) == 1
    assert "required_with_acceptance_tests:entry-point" in warnings[0]
    assert "without conformance tests" in warnings[0]


def test_nothing_is_warned_when_every_functionality_has_a_scenario(project, tmp_path, monkeypatch):
    module = _build(project, tmp_path)

    warnings = []
    monkeypatch.setattr(plain2code.console, "warning", lambda message: warnings.append(message))
    plain2code.warn_about_functionalities_without_scenarios(module)

    assert warnings == []
