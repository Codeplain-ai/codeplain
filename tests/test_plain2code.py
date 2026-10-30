import tempfile
from argparse import Namespace
from types import SimpleNamespace
from unittest.mock import patch

import plain2code
import plain_spec
from plain_modules import PlainModule


def _make_module(module_name, has_scenarios, required_modules=None):
    """Build a minimal stand-in for a PlainModule.

    A functionality carries scenarios only when the module is expected to have a test plain
    file bound to it, because that is all the warning inspects.
    """
    functionality = {"id": f"{module_name}-functionality", "markdown": f"The {module_name} functionality."}
    if has_scenarios:
        functionality[plain_spec.CONFORMANCE_TESTS] = [{"id": "main-path", "text": "It works."}]

    return SimpleNamespace(
        module_name=module_name,
        plain_source={plain_spec.FUNCTIONAL_REQUIREMENTS: [functionality]},
        all_required_modules=required_modules or [],
    )


def _warn(plain_module, conformance_tests_script=None):
    args = Namespace(conformance_tests_script=conformance_tests_script)
    with patch("plain2code.console") as mock_console:
        plain2code.warn_if_scenarios_without_conformance_script(plain_module, args)
    return mock_console


def test_warns_when_scenarios_present_and_no_conformance_script():
    mock_console = _warn(_make_module("top", has_scenarios=True))

    mock_console.warning.assert_called_once()
    warning_message = mock_console.warning.call_args.args[0]
    assert "top" in warning_message
    assert "no scenario is rendered" in warning_message


def test_no_warning_when_conformance_script_configured():
    mock_console = _warn(_make_module("top", has_scenarios=True), "run_conformance_tests.sh")

    mock_console.warning.assert_not_called()


def test_no_warning_when_no_scenarios():
    mock_console = _warn(_make_module("top", has_scenarios=False))

    mock_console.warning.assert_not_called()


def test_warns_when_an_acceptance_scenario_is_the_only_one():
    plain_module = _make_module("top", has_scenarios=False)
    plain_module.plain_source[plain_spec.FUNCTIONAL_REQUIREMENTS][0][plain_spec.ACCEPTANCE_TESTS] = [
        {"id": "reject-empty-name", "text": "It is rejected."}
    ]

    _warn(plain_module).warning.assert_called_once()


def test_warns_when_scenarios_only_in_required_module():
    required_module = _make_module("dependency", has_scenarios=True)
    plain_module = _make_module("top", has_scenarios=False, required_modules=[required_module])

    mock_console = _warn(plain_module)

    mock_console.warning.assert_called_once()
    assert "dependency" in mock_console.warning.call_args.args[0]


def test_warning_covers_required_modules_for_real_plain_module(get_test_data_path):
    """Integration test: a main module without scenarios that requires a module with a test
    plain file should still trigger the warning, naming the required module. This mirrors the
    dry-run path which builds a real PlainModule."""
    fixtures_dir = get_test_data_path("data/acceptance_tests_warning")
    with tempfile.TemporaryDirectory() as build:
        plain_module = PlainModule(
            "main_requiring_acceptance_tests.plain",
            build,
            [fixtures_dir],
            [f"{fixtures_dir}/tests"],
        )

    mock_console = _warn(plain_module)

    mock_console.warning.assert_called_once()
    assert "required_with_acceptance_tests" in mock_console.warning.call_args.args[0]
