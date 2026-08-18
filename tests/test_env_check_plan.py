"""Validation of check plans returned by the server.

A plan is untrusted input, so these tests focus on what the client refuses to do
with a malformed or hostile plan.
"""

import pytest

from env_check.checks import CHECK_TYPES, InvalidCheckArguments
from env_check.plan import parse_plan
from env_check.types import SEVERITY_ERROR, SEVERITY_WARNING


def make_plan(*checks, advisories=None):
    return {"checks": list(checks), "advisories": advisories or []}


def test_valid_check_is_parsed():
    plan = parse_plan(
        make_plan(
            {
                "id": "python",
                "type": "command_available",
                "severity": "error",
                "description": "Python 3 is installed",
                "args": {"command": "python3", "version_arg": "--version", "min_version": "3.11"},
                "reason": "The unit tests script creates a virtual environment.",
                "remediation": {"darwin": "brew install python@3.11"},
            }
        )
    )

    assert len(plan.checks) == 1
    check = plan.checks[0]
    assert check.type == "command_available"
    assert check.severity == SEVERITY_ERROR
    assert check.args["min_version"] == "3.11"
    assert check.remediation_for("darwin") == "brew install python@3.11"
    assert not plan.dropped


def test_unknown_check_type_is_dropped_not_executed():
    plan = parse_plan(make_plan({"id": "evil", "type": "run_shell_command", "args": {"command": "rm -rf /"}}))

    assert plan.checks == []
    assert any("unsupported check type" in reason for reason in plan.dropped)


@pytest.mark.parametrize(
    "command",
    ["python3; rm -rf /", "/bin/sh", "../../bin/sh", "python3 --version", "$(whoami)", "py`id`"],
)
def test_command_argument_rejects_anything_but_a_bare_name(command):
    plan = parse_plan(make_plan({"id": "c", "type": "command_available", "args": {"command": command}}))

    assert plan.checks == []
    assert plan.dropped


def test_version_argument_is_restricted_to_an_allowlist():
    plan = parse_plan(
        make_plan(
            {
                "id": "c",
                "type": "command_available",
                "args": {"command": "python3", "version_arg": "-c import os; os.system('id')"},
            }
        )
    )

    assert plan.checks == []
    assert any("not an allowed version flag" in reason for reason in plan.dropped)


def test_python_module_argument_rejects_injection():
    plan = parse_plan(
        make_plan({"id": "m", "type": "python_module_importable", "args": {"module": "os; os.system('id')"}})
    )

    assert plan.checks == []


def test_url_argument_rejects_non_http_schemes():
    plan = parse_plan(make_plan({"id": "u", "type": "http_reachable", "args": {"url": "file:///etc/passwd"}}))

    assert plan.checks == []


def test_missing_required_argument_is_dropped():
    plan = parse_plan(make_plan({"id": "e", "type": "env_var_set", "args": {}}))

    assert plan.checks == []
    assert any("missing required argument" in reason for reason in plan.dropped)


def test_unknown_severity_falls_back_to_warning():
    plan = parse_plan(
        make_plan({"id": "e", "type": "env_var_set", "severity": "catastrophic", "args": {"name": "FOO"}})
    )

    assert plan.checks[0].severity == SEVERITY_WARNING


def test_duplicate_checks_are_collapsed():
    entry = {"id": "e", "type": "env_var_set", "args": {"name": "FOO"}}
    plan = parse_plan(make_plan(entry, dict(entry, id="e2")))

    assert len(plan.checks) == 1


def test_advisories_are_parsed_and_untitled_ones_dropped():
    plan = parse_plan(
        make_plan(
            advisories=[
                {"id": "a1", "severity": "error", "title": "No script installs dependencies", "detail": "..."},
                {"id": "a2", "detail": "no title here"},
            ]
        )
    )

    assert len(plan.advisories) == 1
    assert plan.advisories[0].is_blocking
    assert any("missing title" in reason for reason in plan.dropped)


def test_non_object_plan_is_survivable():
    plan = parse_plan("not a plan")

    assert plan.checks == []
    assert plan.advisories == []
    assert plan.dropped


def test_string_remediation_is_accepted():
    plan = parse_plan(
        make_plan({"id": "e", "type": "env_var_set", "args": {"name": "FOO"}, "remediation": "export FOO=bar"})
    )

    assert plan.checks[0].remediation_for("linux") == "export FOO=bar"


def test_every_registered_check_type_validates_its_arguments():
    for check_type in CHECK_TYPES.values():
        with pytest.raises(InvalidCheckArguments):
            check_type.validate("not an object")
        if check_type.required:
            with pytest.raises(InvalidCheckArguments):
                check_type.validate({})
