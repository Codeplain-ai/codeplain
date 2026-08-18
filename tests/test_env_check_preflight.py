"""The preflight orchestration: static rules, execution and failure handling."""

import argparse
import sys
import types

import pytest

from env_check import preflight as preflight_module
from env_check import runner as runner_module
from env_check.report import format_failure_summary
from env_check.runner import run_checks
from env_check.static_rules import build_static_plan
from env_check.types import (
    SEVERITY_ERROR,
    SEVERITY_WARNING,
    STATUS_FAILED,
    STATUS_PASSED,
    STATUS_SKIPPED,
    Advisory,
    CheckResult,
    CheckSpec,
    PreflightReport,
)


def make_args(tmp_path, **overrides):
    args = argparse.Namespace(
        unittests_script=None,
        conformance_tests_script=None,
        prepare_environment_script=None,
        build_folder=str(tmp_path / "plain_modules"),
        verbose=False,
        skip_env_check=False,
        env_check_only=False,
    )
    for key, value in overrides.items():
        setattr(args, key, value)
    return args


class TestStaticRules:
    def test_git_is_always_checked(self, tmp_path):
        checks, _ = build_static_plan(make_args(tmp_path))

        assert any(check.id == "static-git" for check in checks)

    def test_configured_scripts_are_checked_for_executability(self, tmp_path):
        script = tmp_path / "run_unittests.sh"
        script.write_text("#!/bin/bash\n")

        checks, _ = build_static_plan(make_args(tmp_path, unittests_script=str(script)))
        script_checks = [check for check in checks if check.args.get("path") == str(script)]

        assert len(script_checks) == 1
        assert script_checks[0].severity == SEVERITY_ERROR
        assert script_checks[0].args["must_be_executable"] is (sys.platform != "win32")

    def test_unconfigured_scripts_produce_no_checks(self, tmp_path):
        checks, _ = build_static_plan(make_args(tmp_path))

        assert not [check for check in checks if check.id.startswith("static-unittests")]

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX behaviour")
    def test_powershell_script_on_posix_is_a_blocking_advisory(self, tmp_path):
        script = tmp_path / "run_unittests.ps1"
        script.write_text("Write-Host hi\n")

        _, advisories = build_static_plan(make_args(tmp_path, unittests_script=str(script)))

        assert len(advisories) == 1
        assert advisories[0].severity == SEVERITY_ERROR

    def test_build_folder_writability_is_checked(self, tmp_path):
        checks, _ = build_static_plan(make_args(tmp_path))

        writable = [check for check in checks if check.type == "directory_writable"]
        assert len(writable) == 1


class TestRunner:
    def test_results_keep_the_original_order(self):
        checks = [
            CheckSpec(id="a", type="env_var_set", severity=SEVERITY_WARNING, description="a", args={"name": "PATH"}),
            CheckSpec(
                id="b",
                type="command_available",
                severity=SEVERITY_ERROR,
                description="b",
                args={"command": "definitely-not-real-xyz"},
            ),
        ]

        results = run_checks(checks)

        assert [result.check.id for result in results] == ["a", "b"]
        assert results[0].status == STATUS_PASSED
        assert results[1].status == STATUS_FAILED

    def test_unknown_type_is_skipped_rather_than_executed(self):
        checks = [CheckSpec(id="x", type="nope", severity=SEVERITY_ERROR, description="x")]

        results = run_checks(checks)

        assert results[0].status == STATUS_SKIPPED

    def test_a_raising_handler_is_skipped_not_fatal(self, monkeypatch):
        def explode(_args):
            raise RuntimeError("boom")

        monkeypatch.setitem(
            runner_module.CHECK_TYPES,
            "env_var_set",
            types.SimpleNamespace(handler=explode, validate=lambda args: args),
        )

        results = run_checks(
            [CheckSpec(id="x", type="env_var_set", severity=SEVERITY_ERROR, description="x", args={"name": "PATH"})]
        )

        assert results[0].status == STATUS_SKIPPED
        assert "boom" in results[0].detail

    def test_no_checks_is_not_an_error(self):
        assert run_checks([]) == []


class TestReport:
    def test_blocking_failures_drive_the_verdict(self):
        failing = CheckSpec(id="a", type="env_var_set", severity=SEVERITY_ERROR, description="A is set")
        warning = CheckSpec(id="b", type="env_var_set", severity=SEVERITY_WARNING, description="B is set")

        report = PreflightReport(
            results=[
                CheckResult(failing, STATUS_FAILED, "A is not set"),
                CheckResult(warning, STATUS_FAILED, "B is not set"),
            ]
        )

        assert report.has_blocking_findings
        assert len(report.blocking_failures) == 1
        assert len(report.warnings) == 1

    def test_warnings_alone_do_not_block(self):
        warning = CheckSpec(id="b", type="env_var_set", severity=SEVERITY_WARNING, description="B is set")
        report = PreflightReport(results=[CheckResult(warning, STATUS_FAILED, "B is not set")])

        assert not report.has_blocking_findings

    def test_blocking_advisory_blocks_without_any_failed_check(self):
        report = PreflightReport(
            advisories=[Advisory(id="a", severity=SEVERITY_ERROR, title="No dependency install", detail="...")]
        )

        assert report.has_blocking_findings

    def test_skipped_checks_never_block(self):
        check = CheckSpec(id="a", type="env_var_set", severity=SEVERITY_ERROR, description="A is set")
        report = PreflightReport(results=[CheckResult(check, STATUS_SKIPPED, "could not run")])

        assert not report.has_blocking_findings

    def test_failure_summary_lists_findings_and_the_escape_hatch(self):
        check = CheckSpec(
            id="a",
            type="command_available",
            severity=SEVERITY_ERROR,
            description="java is installed",
            remediation={"default": "brew install openjdk@17"},
        )
        report = PreflightReport(results=[CheckResult(check, STATUS_FAILED, "'java' was not found on PATH")])

        summary = format_failure_summary(report)

        assert "java is installed" in summary
        assert "brew install openjdk@17" in summary
        assert "--skip-env-check" in summary


class TestPlanRequest:
    def test_server_failure_falls_back_to_the_static_layer(self, tmp_path, monkeypatch):
        class FailingAPI:
            def check_environment(self, context, run_state):
                raise RuntimeError("connection refused")

        monkeypatch.setattr(preflight_module, "build_environment_context", lambda plain_module, args: {"modules": []})

        report = preflight_module.run_environment_preflight(FailingAPI(), object(), make_args(tmp_path), None)

        assert report.plan_unavailable_reason is not None
        assert "connection refused" in report.plan_unavailable_reason
        # The deterministic checks still ran.
        assert report.results

    def test_context_assembly_failure_is_survivable(self, tmp_path, monkeypatch):
        def explode(plain_module, args):
            raise RuntimeError("bad resource")

        monkeypatch.setattr(preflight_module, "build_environment_context", explode)

        report = preflight_module.run_environment_preflight(object(), object(), make_args(tmp_path), None)

        assert "bad resource" in report.plan_unavailable_reason
        assert report.results

    def test_planned_checks_are_merged_with_static_ones(self, tmp_path, monkeypatch):
        class PlanningAPI:
            def check_environment(self, context, run_state):
                return {
                    "checks": [
                        {
                            "id": "chat-port",
                            "type": "env_var_set",
                            "severity": "error",
                            "description": "CHAT_PORT is set",
                            "args": {"name": "PREFLIGHT_UNSET_VAR_XYZ"},
                        }
                    ]
                }

        monkeypatch.setattr(preflight_module, "build_environment_context", lambda plain_module, args: {"modules": []})
        monkeypatch.delenv("PREFLIGHT_UNSET_VAR_XYZ", raising=False)

        report = preflight_module.run_environment_preflight(PlanningAPI(), object(), make_args(tmp_path), None)

        assert report.plan_unavailable_reason is None
        assert any(result.check.id == "chat-port" for result in report.results)
        assert report.has_blocking_findings
