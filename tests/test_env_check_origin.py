"""Static vs dynamic checks: where each one came from, and how that is reported."""

import argparse

from env_check import preflight as preflight_module
from env_check.plan import parse_plan
from env_check.report import print_report
from env_check.static_rules import build_static_plan
from env_check.types import (
    ORIGIN_DYNAMIC,
    ORIGIN_STATIC,
    SEVERITY_ERROR,
    SEVERITY_WARNING,
    STATUS_FAILED,
    STATUS_PASSED,
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


class TestOriginTagging:
    def test_every_static_check_is_tagged_static(self, tmp_path):
        script = tmp_path / "run.sh"
        script.write_text("#!/bin/bash\n")

        checks, _ = build_static_plan(make_args(tmp_path, unittests_script=str(script)))

        assert checks
        assert all(check.origin == ORIGIN_STATIC for check in checks)

    def test_static_advisories_are_tagged_static(self, tmp_path):
        script = tmp_path / "run.ps1"
        script.write_text("Write-Host hi\n")

        _, advisories = build_static_plan(make_args(tmp_path, unittests_script=str(script)))

        assert all(advisory.origin == ORIGIN_STATIC for advisory in advisories)

    def test_every_planned_check_is_tagged_dynamic(self):
        plan = parse_plan(
            {
                "checks": [{"id": "java", "type": "command_available", "args": {"command": "javac"}}],
                "advisories": [{"id": "deps", "title": "No dependency install", "detail": "..."}],
            }
        )

        assert plan.checks[0].origin == ORIGIN_DYNAMIC
        assert plan.advisories[0].origin == ORIGIN_DYNAMIC

    def test_a_report_counts_each_origin_separately(self):
        static_check = CheckSpec(
            id="git", type="command_available", severity=SEVERITY_ERROR, description="git", origin=ORIGIN_STATIC
        )
        dynamic_check = CheckSpec(
            id="java", type="command_available", severity=SEVERITY_ERROR, description="java", origin=ORIGIN_DYNAMIC
        )
        report = PreflightReport(
            results=[
                CheckResult(static_check, STATUS_PASSED, "ok"),
                CheckResult(dynamic_check, STATUS_PASSED, "ok"),
                CheckResult(dynamic_check, STATUS_FAILED, "missing"),
            ]
        )

        assert report.count_by_origin(ORIGIN_STATIC) == 1
        assert report.count_by_origin(ORIGIN_DYNAMIC) == 2
        assert report.describe_composition() == "1 static and 2 dynamic"


class TestOriginInOutput:
    def test_the_summary_names_both_kinds(self, capsys):
        check = CheckSpec(
            id="git", type="command_available", severity=SEVERITY_ERROR, description="git", origin=ORIGIN_STATIC
        )
        report = PreflightReport(results=[CheckResult(check, STATUS_PASSED, "found")])

        print_report(report)

        assert "1 static and 0 dynamic" in capsys.readouterr().out

    def test_a_finding_says_which_kind_of_check_produced_it(self, capsys):
        check = CheckSpec(
            id="java",
            type="command_available",
            severity=SEVERITY_ERROR,
            description="A Java 17 JDK is installed",
            origin=ORIGIN_DYNAMIC,
        )
        report = PreflightReport(results=[CheckResult(check, STATUS_FAILED, "'javac' was not found on PATH")])

        print_report(report)
        output = capsys.readouterr().out

        assert "dynamic" in output
        assert "A Java 17 JDK is installed" in output

    def test_an_advisory_says_which_kind_of_check_produced_it(self, capsys):
        report = PreflightReport(
            advisories=[
                Advisory(
                    id="deps",
                    severity=SEVERITY_WARNING,
                    title="No script installs dependencies",
                    detail="...",
                    origin=ORIGIN_DYNAMIC,
                )
            ]
        )

        print_report(report)

        assert "dynamic" in capsys.readouterr().out

    def test_the_report_never_mentions_how_dynamic_checks_are_produced(self, capsys):
        check = CheckSpec(
            id="java",
            type="command_available",
            severity=SEVERITY_ERROR,
            description="A Java 17 JDK is installed",
            origin=ORIGIN_DYNAMIC,
        )
        report = PreflightReport(results=[CheckResult(check, STATUS_FAILED, "not found")])

        print_report(report)
        output = capsys.readouterr().out.lower()

        assert "llm" not in output
        assert "model" not in output


class TestUnavailablePlan:
    def test_only_static_checks_run_when_no_plan_arrives(self, tmp_path, monkeypatch):
        class FailingAPI:
            def check_environment(self, context, run_state):
                raise RuntimeError("offline")

        monkeypatch.setattr(preflight_module, "build_environment_context", lambda plain_module, args: {"modules": []})

        report = preflight_module.run_environment_preflight(FailingAPI(), object(), make_args(tmp_path), None)

        assert report.count_by_origin(ORIGIN_DYNAMIC) == 0
        assert report.count_by_origin(ORIGIN_STATIC) == len(report.results)
