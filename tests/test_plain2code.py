import tempfile
from argparse import Namespace
from types import SimpleNamespace
from unittest.mock import patch

import plain2code
import plain_spec
from plain_modules import PlainModule


def _make_module(module_name, has_acceptance_tests, required_modules=None):
    """Build a minimal stand-in for a PlainModule.

    The functional requirements only need an `acceptance_tests` key when the module
    is expected to contain acceptance tests, because that is all
    `plain_spec.has_acceptance_tests` inspects.
    """
    functional_requirement = {"markdown": f"- {module_name} functionality."}
    if has_acceptance_tests:
        functional_requirement[plain_spec.ACCEPTANCE_TESTS] = [{"markdown": "- Test it."}]

    return SimpleNamespace(
        module_name=module_name,
        plain_source={plain_spec.FUNCTIONAL_REQUIREMENTS: [functional_requirement]},
        all_required_modules=required_modules or [],
    )


def test_warns_when_acceptance_tests_present_and_no_conformance_script():
    plain_module = _make_module("top", has_acceptance_tests=True)
    args = Namespace(conformance_tests_script=None)

    with patch("plain2code.console") as mock_console:
        plain2code.warn_if_acceptance_tests_without_conformance_script(plain_module, args)

    mock_console.warning.assert_called_once()
    warning_message = mock_console.warning.call_args.args[0]
    assert "top" in warning_message
    assert "conformance tests script" in warning_message


def test_no_warning_when_conformance_script_configured():
    plain_module = _make_module("top", has_acceptance_tests=True)
    args = Namespace(conformance_tests_script="run_conformance_tests.sh")

    with patch("plain2code.console") as mock_console:
        plain2code.warn_if_acceptance_tests_without_conformance_script(plain_module, args)

    mock_console.warning.assert_not_called()


def test_no_warning_when_no_acceptance_tests():
    plain_module = _make_module("top", has_acceptance_tests=False)
    args = Namespace(conformance_tests_script=None)

    with patch("plain2code.console") as mock_console:
        plain2code.warn_if_acceptance_tests_without_conformance_script(plain_module, args)

    mock_console.warning.assert_not_called()


def test_warns_when_acceptance_tests_only_in_required_module():
    required_module = _make_module("dependency", has_acceptance_tests=True)
    plain_module = _make_module("top", has_acceptance_tests=False, required_modules=[required_module])
    args = Namespace(conformance_tests_script=None)

    with patch("plain2code.console") as mock_console:
        plain2code.warn_if_acceptance_tests_without_conformance_script(plain_module, args)

    mock_console.warning.assert_called_once()
    warning_message = mock_console.warning.call_args.args[0]
    assert "dependency" in warning_message


def test_warning_covers_required_modules_for_real_plain_module(get_test_data_path):
    """Integration test: a main module without acceptance tests that requires a
    module with acceptance tests should still trigger the warning, naming the
    required module. This mirrors the dry-run path which builds a real PlainModule."""
    fixtures_dir = get_test_data_path("data/acceptance_tests_warning")
    with tempfile.TemporaryDirectory() as build:
        plain_module = PlainModule(
            "main_requiring_acceptance_tests.plain",
            build,
            [fixtures_dir],
        )

    args = Namespace(conformance_tests_script=None)
    with patch("plain2code.console") as mock_console:
        plain2code.warn_if_acceptance_tests_without_conformance_script(plain_module, args)

    mock_console.warning.assert_called_once()
    warning_message = mock_console.warning.call_args.args[0]
    assert "required_with_acceptance_tests" in warning_message
