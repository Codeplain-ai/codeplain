"""Tests for handing the conformance tests fixer's implementation changes to the unit tests fixer.

When the conformance tests fixer changes implementation code, the unit tests are re-run and fixed next.
The unit tests fixer must know about that change so it adjusts the unit tests instead of reverting it.
"""

import os
import tempfile
from types import SimpleNamespace

import pytest

import plain_spec
from memory_management import MemoryManager
from render_machine.actions import fix_unit_tests as fix_unit_tests_module
from render_machine.actions.fix_conformance_test import FixConformanceTest
from render_machine.actions.fix_unit_tests import FixUnitTests
from render_machine.implementation_code_helpers import ImplementationCodeHelpers
from render_machine.render_types import ConformanceTestsRunningContext, UnitTestsRunningContext


class FakeCodeplainAPI:
    def __init__(self, conformance_fix_response=None, unittests_fix_response=None):
        self.conformance_fix_response = conformance_fix_response
        self.unittests_fix_response = unittests_fix_response or {}
        self.unittests_fix_calls = []

    def fix_conformance_tests_issue(self, *args, **kwargs):
        return self.conformance_fix_response

    def fix_unittests_issue(self, *args, **kwargs):
        self.unittests_fix_calls.append(kwargs)
        return self.unittests_fix_response


class FakeConformanceTests:
    def fetch_existing_conformance_test_files(self, *args):
        return {}, {}

    def store_conformance_tests_files(self, *args):
        pass


@pytest.fixture
def build_folder():
    with tempfile.TemporaryDirectory() as folder:
        with open(os.path.join(folder, "app.py"), "w", encoding="utf-8") as source_file:
            source_file.write("def add(a, b):\n    return a + b\n")
        yield folder


@pytest.fixture
def memory_folder():
    with tempfile.TemporaryDirectory() as folder:
        yield folder


@pytest.fixture(autouse=True)
def isolate_from_git_and_console(monkeypatch):
    monkeypatch.setattr(ImplementationCodeHelpers, "get_code_diff", staticmethod(lambda *args: {}))
    monkeypatch.setattr(plain_spec, "collect_linked_resources", lambda *args: None)
    monkeypatch.setattr(fix_unit_tests_module.render_utils, "print_inputs", lambda *args: None)


def make_conformance_context():
    return ConformanceTestsRunningContext(
        current_testing_module_name="mod",
        current_testing_frid="1",
        fix_attempts=0,
        conformance_tests_json={"1": {"folder_name": "conformance_1"}},
        conformance_tests_render_attempts=0,
        current_testing_frid_specifications={},
        should_prepare_testing_environment=False,
        frid_being_implemented="1",
    )


def make_render_context(api, build_folder, memory_folder, conformance_tests_running_context):
    return SimpleNamespace(
        codeplain_api=api,
        build_folder=build_folder,
        memory_manager=MemoryManager(api, memory_folder),
        conformance_tests=FakeConformanceTests(),
        conformance_tests_running_context=conformance_tests_running_context,
        unit_tests_running_context=UnitTestsRunningContext(fix_attempts=1),
        plain_source_tree={},
        module_name="mod",
        required_modules=None,
        frid_context=SimpleNamespace(frid="1", linked_resources={}),
        get_required_modules_functionalities=lambda: {},
        run_state=SimpleNamespace(render_id="test-render-id", unittest_batch_id=1),
    )


def run_conformance_fix(render_context):
    return FixConformanceTest().execute(render_context, {"previous_conformance_tests_issue": "tests failed"})


def test_conformance_fix_of_implementation_code_is_remembered(build_folder, memory_folder):
    api = FakeCodeplainAPI(
        conformance_fix_response=[
            FixConformanceTest.ISSUE_REASON_CODE_IMPLEMENTATION_CODE,
            {"app.py": "def add(a, b):\n    return a + b + 1\n"},
            {"hypothesis": "off by one", "approach": "add one"},
            "prepared issue",
        ]
    )
    ctx = make_conformance_context()
    render_context = make_render_context(api, build_folder, memory_folder, ctx)

    outcome, _ = run_conformance_fix(render_context)

    assert outcome == FixConformanceTest.IMPLEMENTATION_CODE_UPDATED
    assert len(ctx.implementation_code_fixes) == 1
    recorded_fix = ctx.implementation_code_fixes[0]
    assert recorded_fix["hypothesis"] == "off by one"
    assert recorded_fix["approach"] == "add one"
    assert list(recorded_fix["code_diff"]) == ["app.py"]
    assert "+    return a + b + 1" in recorded_fix["code_diff"]["app.py"]


def test_consecutive_implementation_fixes_accumulate(build_folder, memory_folder):
    ctx = make_conformance_context()
    for attempt in range(2):
        api = FakeCodeplainAPI(
            conformance_fix_response=[
                FixConformanceTest.ISSUE_REASON_CODE_IMPLEMENTATION_CODE,
                {"app.py": f"def add(a, b):\n    return a + b + {attempt + 1}\n"},
                {"hypothesis": f"hypothesis {attempt}", "approach": f"approach {attempt}"},
                "prepared issue",
            ]
        )
        render_context = make_render_context(api, build_folder, memory_folder, ctx)
        run_conformance_fix(render_context)

    assert [fix["hypothesis"] for fix in ctx.implementation_code_fixes] == ["hypothesis 0", "hypothesis 1"]


def test_conformance_fix_without_summary_is_still_remembered(build_folder, memory_folder):
    api = FakeCodeplainAPI(
        conformance_fix_response=[
            FixConformanceTest.ISSUE_REASON_CODE_IMPLEMENTATION_CODE,
            {"app.py": "def add(a, b):\n    return a + b + 1\n"},
            None,
            "prepared issue",
        ]
    )
    ctx = make_conformance_context()
    render_context = make_render_context(api, build_folder, memory_folder, ctx)

    run_conformance_fix(render_context)

    assert ctx.implementation_code_fixes[0]["hypothesis"] is None
    assert ctx.implementation_code_fixes[0]["approach"] is None
    assert "app.py" in ctx.implementation_code_fixes[0]["code_diff"]


def test_conformance_fix_of_conformance_tests_is_not_remembered(build_folder, memory_folder):
    api = FakeCodeplainAPI(
        conformance_fix_response=[
            FixConformanceTest.ISSUE_REASON_CODE_CONFORMANCE_TESTS,
            {"test_app.py": "def test_add(): pass\n"},
            {"hypothesis": "wrong assertion", "approach": "fix test"},
            "prepared issue",
        ]
    )
    ctx = make_conformance_context()
    render_context = make_render_context(api, build_folder, memory_folder, ctx)

    outcome, _ = run_conformance_fix(render_context)

    assert outcome == FixConformanceTest.IMPLEMENTATION_CODE_NOT_UPDATED
    assert ctx.implementation_code_fixes == []


def test_implementation_fix_with_no_files_is_not_remembered(build_folder, memory_folder):
    api = FakeCodeplainAPI(
        conformance_fix_response=[
            FixConformanceTest.ISSUE_REASON_CODE_IMPLEMENTATION_CODE,
            {},
            {"hypothesis": "nothing", "approach": "nothing"},
            "prepared issue",
        ]
    )
    ctx = make_conformance_context()
    render_context = make_render_context(api, build_folder, memory_folder, ctx)

    outcome, _ = run_conformance_fix(render_context)

    assert outcome == FixConformanceTest.IMPLEMENTATION_CODE_NOT_UPDATED
    assert ctx.implementation_code_fixes == []


def run_unit_tests_fix(render_context):
    return FixUnitTests().execute(render_context, {"previous_unittests_issue": "1 failed"})


def test_unit_tests_fix_forwards_conformance_fixes(build_folder, memory_folder):
    api = FakeCodeplainAPI(unittests_fix_response={"test_app.py": "def test_add(): pass\n"})
    ctx = make_conformance_context()
    ctx.implementation_code_fixes.append(
        {"hypothesis": "off by one", "approach": "add one", "code_diff": {"app.py": "+    return a + b + 1"}}
    )
    render_context = make_render_context(api, build_folder, memory_folder, ctx)

    outcome, _ = run_unit_tests_fix(render_context)

    assert outcome == FixUnitTests.SUCCESSFUL_OUTCOME
    assert len(api.unittests_fix_calls) == 1
    assert api.unittests_fix_calls[0]["conformance_tests_fixes"] == ctx.implementation_code_fixes
    # The forwarded list is a copy, so later conformance fixes do not mutate what was sent.
    assert api.unittests_fix_calls[0]["conformance_tests_fixes"] is not ctx.implementation_code_fixes


def test_unit_tests_fix_outside_conformance_phase_sends_no_fixes(build_folder, memory_folder):
    api = FakeCodeplainAPI()
    render_context = make_render_context(api, build_folder, memory_folder, None)

    run_unit_tests_fix(render_context)

    assert api.unittests_fix_calls[0]["conformance_tests_fixes"] is None


def test_unit_tests_fix_in_conformance_phase_without_implementation_changes_sends_no_fixes(build_folder, memory_folder):
    api = FakeCodeplainAPI()
    render_context = make_render_context(api, build_folder, memory_folder, make_conformance_context())

    run_unit_tests_fix(render_context)

    assert api.unittests_fix_calls[0]["conformance_tests_fixes"] is None
