"""Tests for fitting unit test script output into the fixer's character budget.

Output over the budget is cut from the middle, so the start (first failures) and the end (summary
lines, lint findings) both reach the fixer, and a warning tells the script author it happened.
"""

import os
import tempfile
from types import SimpleNamespace

import pytest

from render_machine.actions import fix_unit_tests as fix_unit_tests_module
from render_machine.actions.fix_unit_tests import MAX_ISSUE_LENGTH, FixUnitTests
from render_machine.render_types import UnitTestsRunningContext


class FakeCodeplainAPI:
    def __init__(self):
        self.sent_issues = []

    def fix_unittests_issue(
        self,
        frid,
        plain_source_tree,
        linked_resources,
        existing_files_content,
        module_name,
        required_modules,
        unittests_issue,
        run_state,
        conformance_tests_fixes=None,
    ):
        self.sent_issues.append(unittests_issue)
        return {}


@pytest.fixture
def build_folder():
    with tempfile.TemporaryDirectory() as folder:
        with open(os.path.join(folder, "app.py"), "w", encoding="utf-8") as source_file:
            source_file.write("def add(a, b):\n    return a + b\n")
        yield folder


@pytest.fixture
def warnings(monkeypatch):
    logged = []
    monkeypatch.setattr(fix_unit_tests_module.render_utils, "print_inputs", lambda *args: None)
    monkeypatch.setattr(
        fix_unit_tests_module.console, "warning", lambda *args, **kwargs: logged.append(" ".join(map(str, args)))
    )
    return logged


def run_fix(build_folder, unittests_issue):
    api = FakeCodeplainAPI()
    render_context = SimpleNamespace(
        codeplain_api=api,
        build_folder=build_folder,
        unit_tests_running_context=UnitTestsRunningContext(fix_attempts=1),
        plain_source_tree={},
        module_name="mod",
        frid_context=SimpleNamespace(frid="1", linked_resources={}),
        get_required_modules_functionalities=lambda: {},
        run_state=SimpleNamespace(render_id="test-render-id", unittest_batch_id=1),
    )
    FixUnitTests().execute(render_context, {"previous_unittests_issue": unittests_issue})
    return api.sent_issues[0]


def test_output_within_budget_is_sent_whole_without_a_warning(build_folder, warnings):
    unittests_issue = "F" * MAX_ISSUE_LENGTH

    assert run_fix(build_folder, unittests_issue) == unittests_issue
    assert warnings == []


def test_output_over_budget_keeps_start_and_end_and_warns_with_sizes(build_folder, warnings):
    unittests_issue = "1) User is valid\n" + "." * 20_000 + "\napp/models/user.rb:3:1: C: Style/Foo: offense"

    sent_issue = run_fix(build_folder, unittests_issue)

    assert len(sent_issue) <= MAX_ISSUE_LENGTH
    assert sent_issue.startswith("1) User is valid\n")
    assert sent_issue.endswith("app/models/user.rb:3:1: C: Style/Foo: offense")
    assert "characters omitted" in sent_issue
    assert len(warnings) == 1
    assert f"{len(unittests_issue):,} characters" in warnings[0]
    assert f"{MAX_ISSUE_LENGTH:,}-character budget" in warnings[0]
