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
from render_machine.actions.fix_conformance_test import FixConformanceTest
from render_machine.actions.fix_unit_tests import FixUnitTests
from render_machine.implementation_code_helpers import ImplementationCodeHelpers
from render_machine.render_context import RenderContext
from render_machine.render_types import ConformanceTestsRunningContext, ScriptExecutionHistory, UnitTestsRunningContext


class FakeCodeplainAPI:
    """Conformance fixes return a scripted response; every agent session submits a fix on its first turn."""

    def __init__(self, conformance_fix_response=None):
        self.conformance_fix_response = conformance_fix_response
        self.agent_calls = []
        self.sessions_started = 0

    def fix_conformance_tests_issue(self, *args, **kwargs):
        return self.conformance_fix_response

    def agent_start(self, task_type, task_params, frid, module_name, run_state):
        self.sessions_started += 1
        self.agent_calls.append(("start", task_params))
        return self._submit(f"s{self.sessions_started}")

    def agent_continue(self, session_id, tool_results, frid, module_name, run_state):
        self.agent_calls.append(("continue", session_id, tool_results))
        return self._submit(session_id)

    def _submit(self, session_id):
        call_id = f"submit-{len(self.agent_calls)}"
        return {
            "session_id": session_id,
            "status": "tool_calls",
            "calls": [{"id": call_id, "name": "submit_fix", "args": {"changes_made": "fixed"}}],
        }


class FakeRenderContext(SimpleNamespace):
    unit_tests_agent_session = RenderContext.unit_tests_agent_session


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
    monkeypatch.setattr(
        plain_spec,
        "get_specifications_for_frid",
        lambda tree, frid: ({plain_spec.FUNCTIONAL_REQUIREMENTS: ["- Add numbers."]}, None),
    )


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
    return FakeRenderContext(
        codeplain_api=api,
        build_folder=build_folder,
        memory_manager=MemoryManager(api, memory_folder),
        conformance_tests=FakeConformanceTests(),
        conformance_tests_running_context=conformance_tests_running_context,
        unit_tests_running_context=UnitTestsRunningContext(fix_attempts=1),
        plain_source_tree={},
        module_name="mod",
        required_modules=None,
        frid_context=SimpleNamespace(frid="1", linked_resources={}, changed_files=set()),
        get_required_modules_functionalities=lambda: {},
        run_state=SimpleNamespace(render_id="test-render-id", unittest_batch_id=1),
        unittests_script=None,
        script_execution_history=ScriptExecutionHistory(),
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


def run_unit_tests_fix(render_context, issue="1 failed"):
    return FixUnitTests().execute(render_context, {"previous_unittests_issue": issue})


def start_new_unit_test_loop(render_context):
    """What RenderContext.start_unittests_processing does when the unit tests are run again."""
    render_context.unit_tests_running_context = UnitTestsRunningContext(fix_attempts=0)


def conformance_fix(number):
    return {
        "hypothesis": f"hypothesis {number}",
        "approach": f"approach {number}",
        "code_diff": {"app.py": f"+{number}"},
    }


def test_first_unit_test_loop_of_conformance_phase_seeds_the_session_with_the_fixes(build_folder, memory_folder):
    api = FakeCodeplainAPI()
    ctx = make_conformance_context()
    ctx.implementation_code_fixes.append(conformance_fix(1))
    render_context = make_render_context(api, build_folder, memory_folder, ctx)

    outcome, _ = run_unit_tests_fix(render_context)

    assert outcome == FixUnitTests.SUCCESSFUL_OUTCOME
    kind, task_params = api.agent_calls[0]
    assert kind == "start"
    assert task_params["conformance_tests_fixes"] == ctx.implementation_code_fixes
    # The forwarded list is a copy, so later conformance fixes do not mutate what was sent.
    assert task_params["conformance_tests_fixes"] is not ctx.implementation_code_fixes
    # The file the conformance fix changed is seeded although the FRID did not change it.
    assert "app.py" in task_params["relevant_files"]
    assert ctx.unit_tests_agent_session.session_id == "s1"


def test_next_unit_test_loop_of_conformance_phase_continues_the_session_with_only_new_fixes(
    build_folder, memory_folder
):
    api = FakeCodeplainAPI()
    ctx = make_conformance_context()
    ctx.implementation_code_fixes.append(conformance_fix(1))
    render_context = make_render_context(api, build_folder, memory_folder, ctx)
    run_unit_tests_fix(render_context)

    # The fix was accepted; the conformance tests fixer changes the code again and the unit tests fail again.
    ctx.implementation_code_fixes.append(conformance_fix(2))
    start_new_unit_test_loop(render_context)
    run_unit_tests_fix(render_context, issue="2 failed")

    assert api.sessions_started == 1
    kind, session_id, tool_results = api.agent_calls[1]
    assert (kind, session_id) == ("continue", "s1")
    submit_answer = tool_results[-1]
    assert submit_answer["call_id"] == "submit-1"
    assert submit_answer["output"].startswith("Your fix was accepted: the unit tests passed.")
    assert "Conformance Tests Fix below" in submit_answer["output"]
    assert submit_answer["conformance_tests_fixes"] == [conformance_fix(2)]
    assert submit_answer["test_output"] == "2 failed"


def test_retry_within_a_unit_test_loop_says_the_fix_did_not_work(build_folder, memory_folder):
    api = FakeCodeplainAPI()
    ctx = make_conformance_context()
    ctx.implementation_code_fixes.append(conformance_fix(1))
    render_context = make_render_context(api, build_folder, memory_folder, ctx)
    run_unit_tests_fix(render_context)

    run_unit_tests_fix(render_context, issue="still failing")

    submit_answer = api.agent_calls[1][2][-1]
    assert "still fail" in submit_answer["output"]
    # Fix 1 was already shown to the session, so it is not sent again.
    assert "conformance_tests_fixes" not in submit_answer


def test_new_conformance_phase_starts_a_new_session(build_folder, memory_folder):
    api = FakeCodeplainAPI()
    render_context = make_render_context(api, build_folder, memory_folder, make_conformance_context())
    run_unit_tests_fix(render_context)

    # E.g. the functionality is re-rendered from scratch: the conformance tests running context is recreated.
    render_context.conformance_tests_running_context = make_conformance_context()
    start_new_unit_test_loop(render_context)
    run_unit_tests_fix(render_context)

    assert [call[0] for call in api.agent_calls] == ["start", "start"]


def test_unit_tests_outside_conformance_phase_get_no_fixes_and_a_session_per_loop(build_folder, memory_folder):
    api = FakeCodeplainAPI()
    render_context = make_render_context(api, build_folder, memory_folder, None)
    run_unit_tests_fix(render_context)
    start_new_unit_test_loop(render_context)
    run_unit_tests_fix(render_context)

    assert [call[0] for call in api.agent_calls] == ["start", "start"]
    assert all("conformance_tests_fixes" not in call[1] for call in api.agent_calls)


def test_unit_tests_fix_in_conformance_phase_without_implementation_changes_sends_no_fixes(build_folder, memory_folder):
    api = FakeCodeplainAPI()
    render_context = make_render_context(api, build_folder, memory_folder, make_conformance_context())

    run_unit_tests_fix(render_context)

    assert "conformance_tests_fixes" not in api.agent_calls[0][1]
