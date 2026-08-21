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


def _run_main_with(render_side_effect, required_module_count):
    """Drive plain2code.main() far enough to reach its exit path.

    Everything outside the exit path is stubbed: the point is to observe what
    main() reports once the render is over.
    """
    args = Namespace(
        version=False,
        status=False,
        full_plain=False,
        dry_run=False,
        headless=True,
        filename="test.plain",
        template_dir=None,
        build_folder="plain_modules",
        api="https://api.codeplain.ai",
        api_key="test-key",
        replay_with=None,
        render_range=None,
        render_from=None,
        log_to_file=False,
        log_file_name=None,
        unittests_script=None,
        conformance_tests_script=None,
        prepare_environment_script=None,
    )
    required_modules = [SimpleNamespace(cleanup_scratch=lambda: None) for _ in range(required_module_count)]
    plain_module = SimpleNamespace(
        plain_source={},
        all_required_modules=required_modules,
        cleanup_scratch=lambda: None,
    )

    with (
        patch.object(plain2code, "parse_arguments", return_value=args),
        patch.object(plain2code.file_utils, "get_template_directories", return_value=["templates"]),
        patch.object(plain2code.plain_modules, "PlainModule", return_value=plain_module),
        patch.object(plain2code, "setup_logging", return_value="INFO"),
        patch.object(plain2code, "initialize_telemetry"),
        patch.object(plain2code, "print_exit_summary"),
        patch.object(plain2code, "dump_crash_logs"),
        patch.object(plain2code, "capture_crash"),
        patch.object(plain2code, "render", side_effect=render_side_effect) as render_mock,
        patch.object(plain2code, "capture_render_finished") as capture_mock,
        patch.object(plain2code.sys, "exit"),
    ):
        plain2code.main()

    assert render_mock.called
    assert capture_mock.call_count == 1
    return capture_mock.call_args


def test_render_outcome_is_reported_for_a_successful_render():
    call_args = _run_main_with(render_side_effect=None, required_module_count=1)

    assert call_args.kwargs["module_count"] == 2
    assert call_args.kwargs["error_type"] is None
    assert call_args.kwargs["crashed"] is False


def test_render_outcome_is_reported_for_an_expected_failure():
    call_args = _run_main_with(
        render_side_effect=plain2code.PlainSyntaxError("bad spec"),
        required_module_count=0,
    )

    assert call_args.kwargs["module_count"] == 1
    assert call_args.kwargs["error_type"] == "PlainSyntaxError"
    # An expected error is a failed render, not a crash.
    assert call_args.kwargs["crashed"] is False


def test_render_outcome_is_reported_for_an_unexpected_crash():
    call_args = _run_main_with(render_side_effect=RuntimeError("boom"), required_module_count=0)

    assert call_args.kwargs["error_type"] == "RuntimeError"
    assert call_args.kwargs["crashed"] is True
