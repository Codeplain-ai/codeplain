"""The test folders: where they come from, and what the client does with the scenarios it finds there."""

import os

import pytest

import file_utils  # noqa: F401  # imported before plain_modules, which it imports back from
from plain2code_arguments import parse_arguments
from plain_modules import PlainModule

FIXTURES = "data/acceptance_tests_warning"
MAIN_MODULE = "main_requiring_acceptance_tests.plain"
REQUIRED_MODULE = "required_with_acceptance_tests.plain"


@pytest.fixture
def fixtures_dir(get_test_data_path):
    return get_test_data_path(FIXTURES)


@pytest.fixture
def scenarios_dir(fixtures_dir):
    return os.path.join(fixtures_dir, "tests")


def _parse(tmp_path, *extra_args):
    plain_file = tmp_path / "project.plain"
    plain_file.write_text("***functional specs***\n\n# do-something\n:App: does something.\n")
    return parse_arguments([str(plain_file), *extra_args])


def test_there_is_no_default_test_dir(tmp_path):
    args = _parse(tmp_path)

    assert args.test_dir is None


def test_a_conformance_tests_script_without_a_test_dir_is_a_usage_error(tmp_path):
    script = tmp_path / "run_conformance_tests.sh"
    script.write_text("#!/bin/sh\n")

    with pytest.raises(SystemExit):
        _parse(tmp_path, "--conformance-tests-script", str(script))


def test_a_conformance_tests_script_with_a_test_dir_is_accepted(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    script = tmp_path / "run_conformance_tests.sh"
    script.write_text("#!/bin/sh\n")

    args = _parse(tmp_path, "--conformance-tests-script", str(script), "--test-dir", "scenarios")

    assert args.test_dir == [str(tmp_path / "scenarios")]


def test_test_dir_can_be_given_more_than_once(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    args = _parse(tmp_path, "--test-dir", "scenarios", "--test-dir", "extra")

    assert args.test_dir == [str(tmp_path / "scenarios"), str(tmp_path / "extra")]


def test_test_dir_from_the_config_file_resolves_against_the_config_folder(tmp_path):
    (tmp_path / "config.yaml").write_text("test-dir: scenarios\n")
    args = _parse(tmp_path)

    assert args.test_dir == [str(tmp_path / "scenarios")]


def test_scenarios_of_a_test_plain_file_reach_the_functionality(tmp_path, fixtures_dir, scenarios_dir):
    module = PlainModule(REQUIRED_MODULE, str(tmp_path / "build"), [fixtures_dir], [scenarios_dir])

    functionality = module.plain_source["functional specs"][0]
    assert [scenario["id"] for scenario in functionality["conformance_tests"]] == ["runs-without-errors"]


def test_a_functionality_without_a_conformance_scenario_stops_the_run(tmp_path, fixtures_dir, scenarios_dir):
    with pytest.raises(Exception, match="main_requiring_acceptance_tests:display-hello"):
        PlainModule(
            MAIN_MODULE,
            str(tmp_path / "build"),
            [fixtures_dir],
            [scenarios_dir],
            conformance_tests_enabled=True,
        )


def test_the_run_goes_on_without_a_scenario_when_conformance_tests_are_off(tmp_path, fixtures_dir, scenarios_dir):
    module = PlainModule(MAIN_MODULE, str(tmp_path / "build"), [fixtures_dir], [scenarios_dir])

    assert module.module_name == "main_requiring_acceptance_tests"
