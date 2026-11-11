"""Tests for fixing conformance tests through a conversation between a fixer agent and a reviewer agent."""

import os
from types import SimpleNamespace

import pytest

import plain_spec
from memory_management import MemoryManager
from plain2code_exceptions import ConflictingRequirements
from plain2code_utils import qualified_frid
from render_machine import conformance_fix_conversation as conversation
from render_machine.actions.fix_conformance_test import FixConformanceTest
from render_machine.actions.run_conformance_tests import RunConformanceTests
from render_machine.implementation_code_helpers import ImplementationCodeHelpers
from render_machine.render_types import ConformanceTestsRunningContext, ScriptExecutionHistory

ISSUE = "FAILED test_app.py::test_add - assert 2 == 3"


def write(path, content):
    return {"name": "write_file", "args": {"file_path": path, "content": content}}


def submit_fix(failing_reason="IMPLEMENTATION_CODE", **args):
    return {
        "name": "submit_fix",
        "args": {"root_cause": "off by one", "changes_made": "add one", "failing_reason": failing_reason, **args},
    }


def review(verdict, comments="", objected_files=None):
    args = {"verdict": verdict, "comments": comments}
    if objected_files is not None:
        args["objected_files"] = objected_files
    return {"name": "submit_review", "args": args}


class ScriptedAgentAPI:
    """Plays the server side of the agent sessions: each task type answers with its scripted turns in order.
    A turn is a list of tool calls (each turn of the server asking for them), or a raw response dict."""

    def __init__(self, fixer_turns=(), reviewer_turns=()):
        self.turns = {conversation.FIXER_TASK: list(fixer_turns), conversation.REVIEWER_TASK: list(reviewer_turns)}
        self.session_tasks = {}
        self.calls = []
        self.conflict_reports = []

    def agent_start(self, task_type, task_params, frid, module_name, run_state):
        session_id = f"{task_type}-{len(self.session_tasks) + 1}"
        self.session_tasks[session_id] = task_type
        self.calls.append(("start", task_type, session_id, task_params))
        return self._next(session_id)

    def agent_continue(self, session_id, tool_results, frid, module_name, run_state):
        self.calls.append(("continue", self.session_tasks[session_id], session_id, tool_results))
        return self._next(session_id)

    def report_conflicting_requirements(self, *args, **kwargs):
        self.conflict_reports.append(args)
        raise ConflictingRequirements("Conflicting requirements: they conflict")

    def _next(self, session_id):
        turn = self.turns[self.session_tasks[session_id]].pop(0)
        if isinstance(turn, dict):
            return {"session_id": session_id, **turn}
        calls = [{"id": f"call-{len(self.calls)}-{index}", **call} for index, call in enumerate(turn)]
        return {"session_id": session_id, "status": "tool_calls", "calls": calls}

    def of(self, kind, task_type):
        return [call for call in self.calls if call[0] == kind and call[1] == task_type]


class FakeConformanceTests:
    def __init__(self, base):
        self.base = base

    def get_module_conformance_tests_folder(self, module_name):
        return str(self.base / "conformance") if module_name == "mod" else str(self.base / f"conformance_{module_name}")

    def get_source_conformance_test_folder_name(self, module_name, required_modules, testing_module, folder):
        return folder, folder + "_copy"

    def get_conformance_tests_json(self, module_name):
        return getattr(self, "tests_json", {}).get(module_name, {})


@pytest.fixture
def project(tmp_path, monkeypatch):
    build = tmp_path / "build"
    build.mkdir()
    (build / "app.py").write_text("def add(a, b):\n    return a + b\n")
    tests_folder = tmp_path / "conformance" / "add"
    tests_folder.mkdir(parents=True)
    (tests_folder / "test_app.py").write_text("def test_add():\n    assert add(1, 2) == 3\n")
    memory = tmp_path / "memory"
    memory.mkdir()
    monkeypatch.setattr(ImplementationCodeHelpers, "get_code_diff", staticmethod(lambda *args: {"app.py": "+x"}))
    monkeypatch.setattr(
        plain_spec,
        "get_specifications_for_frid",
        lambda tree, frid: (
            {
                plain_spec.DEFINITIONS: ["- :Number: is a number."],
                plain_spec.FUNCTIONAL_REQUIREMENTS: ["- Add numbers."],
                plain_spec.ACCEPTANCE_TESTS: [{"id": "a1", "text": "1 + 2 is 3"}],
            },
            None,
        ),
    )
    return SimpleNamespace(root=tmp_path, build=build, tests_folder=tests_folder, memory=memory)


def make_render_context(project, api, testing_frid="1", frid="1", testing_module="mod", required_module_names=()):
    ctx = ConformanceTestsRunningContext(
        current_testing_module_name=testing_module,
        current_testing_frid=testing_frid,
        fix_attempts=0,
        conformance_tests_json={testing_frid: {"folder_name": str(project.tests_folder)}},
        conformance_tests_render_attempts=0,
        current_testing_frid_specifications={
            plain_spec.FUNCTIONAL_REQUIREMENTS: ["- Add numbers."],
            plain_spec.CONFORMANCE_TESTS: [{"id": "s1", "text": "adding 1 and 2 gives 3"}],
        },
        should_prepare_testing_environment=False,
        frid_being_implemented=frid,
    )
    return SimpleNamespace(
        codeplain_api=api,
        build_folder=str(project.build),
        memory_manager=MemoryManager(api, str(project.memory)),
        conformance_tests=FakeConformanceTests(project.root),
        plain_module=SimpleNamespace(
            build_folder=str(project.root / "plain_modules"),
            all_required_modules=[SimpleNamespace(module_name=name) for name in required_module_names],
        ),
        conformance_tests_running_context=ctx,
        plain_source_tree={},
        module_name="mod",
        required_modules=[],
        frid_context=SimpleNamespace(frid=frid, linked_resources={}, changed_files={"app.py"}),
        get_required_modules_functionalities=lambda: {},
        testing_functionality_label=lambda: f"{ctx.current_testing_module_name}:{ctx.current_testing_frid}",
        testing_functionality_position=lambda: None,
        run_state=SimpleNamespace(render_id="render-id"),
        conformance_tests_script=None,
        prepare_environment_script=None,
        script_execution_history=ScriptExecutionHistory(),
        test_script_timeout=None,
        stop_event=None,
    )


def run_fix(render_context, issue=ISSUE):
    return FixConformanceTest().execute(render_context, {"previous_conformance_tests_issue": issue})


def test_approved_implementation_fix_runs_the_unit_tests_next_and_is_handed_to_the_unit_tests_fixer(project):
    api = ScriptedAgentAPI(
        fixer_turns=[[write("app.py", "def add(a, b):\n    return a + b + 1\n")], [submit_fix()]],
        reviewer_turns=[[review("approve", "Addresses the root cause.")]],
    )
    render_context = make_render_context(project, api)

    outcome, _ = run_fix(render_context)

    ctx = render_context.conformance_tests_running_context
    assert outcome == FixConformanceTest.IMPLEMENTATION_CODE_UPDATED
    assert ctx.should_prepare_testing_environment and ctx.test_that_triggered_code_change == ("mod", "1")
    [recorded] = ctx.implementation_code_fixes
    assert recorded["hypothesis"] == "off by one" and recorded["approach"] == "add one"
    assert list(recorded["code_diff"]) == ["app.py"] and "+    return a + b + 1" in recorded["code_diff"]["app.py"]

    [(_, _, _, fixer_params)] = api.of("start", conversation.FIXER_TASK)
    assert fixer_params["conformance_tests_issue"] == ISSUE
    assert fixer_params["conformance_tests_folder"] == str(project.tests_folder)
    assert fixer_params["is_regression"] is False and "code_diff" not in fixer_params
    assert "#### Scenario `s1`" in fixer_params["conformance_scenarios"]
    assert "#### Scenario `a1`" in fixer_params["acceptance_tests"]
    assert str(project.tests_folder / "test_app.py") in fixer_params["conformance_tests_files"]
    assert "app.py" in fixer_params["relevant_files"]

    [(_, _, _, reviewer_params)] = api.of("start", conversation.REVIEWER_TASK)
    fix_submission = reviewer_params["fix_submission"]
    assert reviewer_params["conformance_tests_issue"] == ISSUE
    assert (
        fix_submission["changes_made"] == "add one" and "+    return a + b + 1" in fix_submission["code_diff"]["app.py"]
    )
    # The reviewer sees the conformance tests as they were before the fix.
    assert (
        "assert add(1, 2) == 3" in reviewer_params["conformance_tests_files"][str(project.tests_folder / "test_app.py")]
    )

    agents = ctx.conformance_fix_agents
    assert agents.fixer.pending_call_id and agents.reviewer.pending_call_id and agents.approved_fix_tested


def test_conformance_tests_fix_runs_the_conformance_tests_next(project):
    test_file = str(project.tests_folder / "test_app.py")
    api = ScriptedAgentAPI(
        fixer_turns=[
            [write(test_file, "def test_add():\n    assert add(1, 2) == 4\n")],
            [submit_fix("CONFORMANCE_TESTS")],
        ],
        reviewer_turns=[[review("approve")]],
    )
    render_context = make_render_context(project, api)

    outcome, _ = run_fix(render_context)

    assert outcome == FixConformanceTest.IMPLEMENTATION_CODE_NOT_UPDATED
    assert render_context.conformance_tests_running_context.implementation_code_fixes == []
    assert "== 4" in open(test_file).read()
    [(_, _, _, reviewer_params)] = api.of("start", conversation.REVIEWER_TASK)
    assert list(reviewer_params["fix_submission"]["code_diff"]) == [test_file]


def _sessions_ending_without_an_approved_fix(first_turn):
    ended = {"status": "completed", "result": "handoff"}
    return [first_turn, ended] + [ended] * (conversation.MAX_FIXER_SESSIONS_PER_FIX - 1)


def test_unapproved_implementation_changes_are_kept_and_tested_after_the_unit_tests(project):
    # The fixer never got a fix approved: its changes stay, and like an approved implementation change they are
    # tested after the unit tests and a fresh preparation, never on the environment prepared for the older code.
    api = ScriptedAgentAPI(
        fixer_turns=_sessions_ending_without_an_approved_fix([write("app.py", "def add(a, b):\n    return 3\n")])
    )
    render_context = make_render_context(project, api)

    outcome, _ = run_fix(render_context)

    ctx = render_context.conformance_tests_running_context
    assert len(api.of("start", conversation.FIXER_TASK)) == conversation.MAX_FIXER_SESSIONS_PER_FIX
    assert not api.of("start", conversation.REVIEWER_TASK)
    assert outcome == FixConformanceTest.IMPLEMENTATION_CODE_UPDATED
    assert "return 3" in (project.build / "app.py").read_text()
    assert ctx.should_prepare_testing_environment and ctx.test_that_triggered_code_change == ("mod", "1")
    # Handed to the unit tests fixer, so it keeps the change instead of reverting it.
    [recorded] = ctx.implementation_code_fixes
    assert "without getting them approved" in recorded["approach"]
    assert "+    return 3" in recorded["code_diff"]["app.py"]


def test_unapproved_changes_to_the_conformance_tests_only_run_the_conformance_tests_again(project):
    test_file = str(project.tests_folder / "test_app.py")
    api = ScriptedAgentAPI(
        fixer_turns=_sessions_ending_without_an_approved_fix([write(test_file, "def test_add():\n    pass\n")])
    )
    render_context = make_render_context(project, api)

    outcome, _ = run_fix(render_context)

    ctx = render_context.conformance_tests_running_context
    assert outcome == FixConformanceTest.IMPLEMENTATION_CODE_NOT_UPDATED
    assert "pass" in open(test_file).read()
    assert not ctx.should_prepare_testing_environment and ctx.implementation_code_fixes == []


def test_rejected_fix_goes_back_to_the_fixer_with_the_comments_and_the_revision_is_reviewed(project):
    api = ScriptedAgentAPI(
        fixer_turns=[
            [write("app.py", "def add(a, b):\n    return 3\n")],
            [submit_fix()],
            [write("app.py", "def add(a, b):\n    return a + b\n# fixed\n")],
            [submit_fix(review_response="Implemented addition instead of a constant.")],
        ],
        reviewer_turns=[[review("reject", "Returning a constant only fits the test.")], [review("approve")]],
    )
    render_context = make_render_context(project, api)

    outcome, _ = run_fix(render_context)

    assert outcome == FixConformanceTest.IMPLEMENTATION_CODE_UPDATED
    fixer_answers = [results[-1] for _, _, _, results in api.of("continue", conversation.FIXER_TASK)]
    [rejection] = [answer for answer in fixer_answers if "review" in answer]
    assert rejection["output"] == conversation.REJECTED_MESSAGE
    assert rejection["review"] == {"comments": "Returning a constant only fits the test."}

    [(_, _, _, reviewer_results)] = api.of("continue", conversation.REVIEWER_TASK)
    revision = reviewer_results[-1]
    assert revision["output"] == conversation.REVIEW_REVISION_MESSAGE and "test_output" not in revision
    assert revision["fix_submission"]["review_response"] == "Implemented addition instead of a constant."
    # The revision is shown as the whole fix since the tests ran: the constant is gone again.
    assert "+# fixed" in revision["fix_submission"]["code_diff"]["app.py"]
    assert "return 3" not in revision["fix_submission"]["code_diff"]["app.py"]


def test_fix_rejected_too_often_loses_only_the_objected_changes_and_the_sessions_go_on(project):
    rejections = conversation.MAX_REJECTIONS
    fixer_turns = [[write("test_app_extra.py", "x = 1\n"), write("app.py", "def add(a, b):\n    return b + a\n")]]
    fixer_turns += [[submit_fix()] for _ in range(rejections + 1)]
    reviewer_turns = [[review("reject", f"No {number}.", ["test_app_extra.py"])] for number in range(rejections)]
    api = ScriptedAgentAPI(fixer_turns=fixer_turns, reviewer_turns=reviewer_turns + [[review("approve")]])
    render_context = make_render_context(project, api)

    outcome, _ = run_fix(render_context)

    assert outcome == FixConformanceTest.IMPLEMENTATION_CODE_UPDATED
    # Only the objected file was reverted; the other change stays, and nobody started over.
    assert not (project.build / "test_app_extra.py").exists()
    assert "return b + a" in (project.build / "app.py").read_text()
    assert len(api.of("start", conversation.FIXER_TASK)) == 1 and len(api.of("start", conversation.REVIEWER_TASK)) == 1
    answers = [results[-1] for (_, _, _, results) in api.of("continue", conversation.FIXER_TASK)]
    rejected = [a for a in answers if a.get("review")]
    assert "reverted_files" not in rejected[rejections - 2]["review"]
    assert rejected[rejections - 1]["review"]["reverted_files"] == ["test_app_extra.py"]
    # The reviewer is told how many fixes it rejected in a row.
    reviews = [results[-1]["fix_submission"] for (_, _, _, results) in api.of("continue", conversation.REVIEWER_TASK)]
    assert [r["consecutive_rejections"] for r in reviews] == list(range(1, rejections + 1))
    assert list(reviews[-1]["code_diff"]) == ["app.py"]


def test_a_file_read_again_after_it_was_reverted_is_read_from_disk(project):
    rejections = conversation.MAX_REJECTIONS
    read_extra = {"name": "read_file", "args": {"file_path": "test_app_extra.py"}}
    fixer_turns = [[write("test_app_extra.py", "x = 1\n"), write("app.py", "v1\n")], [read_extra]]
    fixer_turns += [[submit_fix()] for _ in range(rejections)] + [[read_extra], [submit_fix()]]
    reviewer_turns = [[review("reject", "No.", ["test_app_extra.py"])] for _ in range(rejections)]
    api = ScriptedAgentAPI(fixer_turns=fixer_turns, reviewer_turns=reviewer_turns + [[review("approve")]])

    run_fix(make_render_context(project, api))

    first_read, second_read = [
        results
        for (_, _, _, results) in api.of("continue", conversation.FIXER_TASK)
        if len(results) == 1 and "review" not in results[0]
    ]
    assert "x = 1" in str(first_read)
    # Not the cached content from before the revert: the file is gone.
    assert "x = 1" not in str(second_read) and "not found" in str(second_read).lower()


def test_fix_that_changes_nothing_is_sent_back_without_a_review(project):
    api = ScriptedAgentAPI(
        fixer_turns=[[submit_fix()], [write("app.py", "def add(a, b):\n    return a + b  # ok\n")], [submit_fix()]],
        reviewer_turns=[[review("approve")]],
    )
    render_context = make_render_context(project, api)

    run_fix(render_context)

    first_answer = api.of("continue", conversation.FIXER_TASK)[0][3][-1]
    assert first_answer["output"] == conversation.EMPTY_FIX_MESSAGE
    assert len(api.of("start", conversation.REVIEWER_TASK)) == 1


def test_tests_still_failing_continue_both_sessions_with_the_new_output(project):
    api = ScriptedAgentAPI(
        fixer_turns=[
            [write("app.py", "v1\n")],
            [submit_fix()],
            [write("app.py", "v2\n")],
            [submit_fix()],
        ],
        reviewer_turns=[[review("approve")], [review("approve")]],
    )
    render_context = make_render_context(project, api)
    run_fix(render_context)

    outcome, _ = run_fix(render_context, issue="STILL FAILING")

    assert outcome == FixConformanceTest.IMPLEMENTATION_CODE_UPDATED
    assert len(api.of("start", conversation.FIXER_TASK)) == 1 and len(api.of("start", conversation.REVIEWER_TASK)) == 1
    fixer_answer = api.of("continue", conversation.FIXER_TASK)[1][3][-1]
    assert fixer_answer["output"].startswith(conversation.TESTS_STILL_FAIL_MESSAGE)
    assert fixer_answer["test_output"] == "STILL FAILING"
    [(_, _, _, reviewer_results)] = api.of("continue", conversation.REVIEWER_TASK)
    assert reviewer_results[-1]["output"].startswith(conversation.REVIEW_AFTER_TESTS_MESSAGE)
    assert reviewer_results[-1]["test_output"] == "STILL FAILING"
    # Only the second fix is shown: the first one was approved and tested.
    assert "-v1" in reviewer_results[-1]["fix_submission"]["code_diff"]["app.py"]


def test_fixer_is_told_when_runs_after_approved_fixes_keep_failing(project, monkeypatch):
    monkeypatch.setattr(conversation, "NOT_CONVERGING_AFTER_FAILED_RUNS", 2)
    fixer_turns, reviewer_turns = [], []
    for number in range(3):
        fixer_turns += [[write("app.py", f"v{number}\n")], [submit_fix()]]
        reviewer_turns.append([review("approve")])
    api = ScriptedAgentAPI(fixer_turns=fixer_turns, reviewer_turns=reviewer_turns)
    render_context = make_render_context(project, api)

    for _ in range(3):
        run_fix(render_context, issue="STILL FAILING")

    answers = [results[-1]["output"] for (_, _, _, results) in api.of("continue", conversation.FIXER_TASK)]
    still_failing = [a for a in answers if a.startswith(conversation.TESTS_STILL_FAIL_MESSAGE)]
    assert len(still_failing) == 2
    assert "not converging" not in still_failing[0]
    assert "failed 2 times in a row" in still_failing[1] and "not converging" in still_failing[1]


def test_passing_tests_end_both_agents(project, monkeypatch):
    api = ScriptedAgentAPI(
        fixer_turns=[[write("app.py", "v1\n")], [submit_fix()]], reviewer_turns=[[review("approve")]]
    )
    render_context = make_render_context(project, api)
    render_context.conformance_tests_script = "run.sh"
    run_fix(render_context)
    monkeypatch.setattr(
        "render_machine.render_utils.execute_script", lambda *args, **kwargs: (0, "", None), raising=True
    )

    outcome, _ = RunConformanceTests().execute(render_context, None)

    assert outcome == RunConformanceTests.SUCCESSFUL_OUTCOME
    assert render_context.conformance_tests_running_context.conformance_fix_agents is None


def test_failing_tests_of_another_functionality_get_new_agents(project):
    api = ScriptedAgentAPI(
        fixer_turns=[[write("app.py", "v1\n")], [submit_fix()], [write("app.py", "v2\n")], [submit_fix()]],
        reviewer_turns=[[review("approve")], [review("approve")]],
    )
    render_context = make_render_context(project, api, testing_frid="1", frid="2")
    run_fix(render_context)
    ctx = render_context.conformance_tests_running_context
    ctx.current_testing_frid = "2"
    ctx.set_conformance_tests_json("mod", {"2": {"folder_name": str(project.tests_folder)}})

    run_fix(render_context)

    assert len(api.of("start", conversation.FIXER_TASK)) == 2
    assert ctx.conformance_fix_agents.tested == ("mod", "2")
    regression_params = api.of("start", conversation.FIXER_TASK)[0][3]
    assert regression_params["is_regression"] is True and regression_params["code_diff"] == {"app.py": "+x"}


def test_agreed_conflict_is_counted_first_and_reported_the_second_time(project):
    conflict = submit_fix("CONFLICTING_REQUIREMENTS", conflict_reason="1 and 2 contradict each other.")
    api = ScriptedAgentAPI(
        fixer_turns=[[write("app.py", "v1\n")], [conflict], [conflict]],
        reviewer_turns=[[review("approve")], [review("approve")]],
    )
    render_context = make_render_context(project, api, testing_frid="1", frid="2")
    ctx = render_context.conformance_tests_running_context

    outcome, _ = run_fix(render_context)
    assert outcome == FixConformanceTest.IMPLEMENTATION_CODE_UPDATED
    assert ctx.conflicting_requirement_count == 1 and api.conflict_reports == []
    # A second declaration in the same session is still reviewed and, once agreed, ends the render.
    assert "conflict_already_reported" not in api.of("start", conversation.FIXER_TASK)[0][3]

    with pytest.raises(ConflictingRequirements):
        run_fix(render_context)
    [report] = api.conflict_reports
    assert report[6] == "CONFLICTING_REQUIREMENTS" and report[7] == "1 and 2 contradict each other."


def test_conflict_of_the_current_functionality_is_with_its_acceptance_tests(project):
    conflict = submit_fix("CONFLICTING_REQUIREMENTS", conflict_reason="a1 contradicts the requirement.")
    api = ScriptedAgentAPI(fixer_turns=[[conflict]], reviewer_turns=[[review("approve")]])
    render_context = make_render_context(project, api)
    ctx = render_context.conformance_tests_running_context
    ctx.conflicting_requirement_count = 1
    ctx.conflicting_module_name, ctx.conflicting_frid = "mod", "1"

    with pytest.raises(ConflictingRequirements):
        run_fix(render_context)

    assert api.conflict_reports[0][6] == "CONFLICTING_ACCEPTANCE_TESTS"
    assert api.of("start", conversation.FIXER_TASK)[0][3]["conflict_already_reported"] is True


def test_reviewer_cannot_change_files_and_fixer_cannot_write_outside_its_folders(project, tmp_path):
    outside = str(tmp_path / "outside.py")
    api = ScriptedAgentAPI(
        fixer_turns=[[write(outside, "x\n"), write("app.py", "v1\n")], [submit_fix()]],
        reviewer_turns=[[write("app.py", "reviewer\n")], [review("approve")]],
    )
    render_context = make_render_context(project, api)

    run_fix(render_context)

    fixer_results = api.of("continue", conversation.FIXER_TASK)[0][3]
    assert fixer_results[0]["output"].startswith("Error: write access denied")
    [(_, _, _, reviewer_results)] = api.of("continue", conversation.REVIEWER_TASK)
    assert reviewer_results[0]["output"].startswith("Error: unknown tool")
    assert (project.build / "app.py").read_text() == "v1\n" and not os.path.exists(outside)


def read(path):
    return {"name": "read_file", "args": {"file_path": path}}


def test_agents_read_the_tests_of_other_functionalities_and_required_modules_but_only_write_their_own(project):
    sibling = project.root / "conformance" / "login" / "test_login.py"
    sibling.parent.mkdir()
    sibling.write_text("WORKING HARNESS\n")
    required = project.root / "conformance_base" / "health" / "test_health.py"
    required.parent.mkdir(parents=True)
    required.write_text("BASE HARNESS\n")
    api = ScriptedAgentAPI(
        fixer_turns=[
            [read(str(sibling)), read(str(required)), write(str(sibling), "x\n"), write("app.py", "v1\n")],
            [submit_fix()],
        ],
        reviewer_turns=[[read(str(sibling)), read(str(required))], [review("approve")]],
    )
    render_context = make_render_context(project, api, required_module_names=["base"])

    run_fix(render_context)

    fixer_results = [r["output"] for r in api.of("continue", conversation.FIXER_TASK)[0][3]]
    assert "WORKING HARNESS" in fixer_results[0] and "BASE HARNESS" in fixer_results[1]
    assert fixer_results[2].startswith("Error: write access denied") and sibling.read_text() == "WORKING HARNESS\n"
    reviewer_results = [r["output"] for r in api.of("continue", conversation.REVIEWER_TASK)[0][3]]
    assert "WORKING HARNESS" in reviewer_results[0] and "BASE HARNESS" in reviewer_results[1]
    [(_, _, _, start_params)] = api.of("start", conversation.FIXER_TASK)
    assert start_params["readable_conformance_tests_folders"] == [
        str(project.root / "conformance"),
        str(project.root / "conformance_base"),
    ]


def test_agents_read_every_rendered_module_and_are_told_the_layout(project):
    other = project.root / "plain_modules" / "base" / "code" / "lib.py"
    other.parent.mkdir(parents=True)
    other.write_text("BASE CODE\n")
    api = ScriptedAgentAPI(
        fixer_turns=[[read(str(other)), write(str(other), "x\n"), write("app.py", "v1\n")], [submit_fix()]],
        reviewer_turns=[[read(str(other))], [review("approve")]],
    )
    render_context = make_render_context(project, api, required_module_names=["base"])

    run_fix(render_context)

    fixer_results = [r["output"] for r in api.of("continue", conversation.FIXER_TASK)[0][3]]
    assert "BASE CODE" in fixer_results[0] and fixer_results[1].startswith("Error: write access denied")
    assert other.read_text() == "BASE CODE\n"
    assert "BASE CODE" in api.of("continue", conversation.REVIEWER_TASK)[0][3][0]["output"]
    [(_, _, _, start_params)] = api.of("start", conversation.FIXER_TASK)
    assert start_params["plain_modules_folder"] == str(project.root / "plain_modules")
    assert start_params["required_module_names"] == ["base"]


@pytest.mark.parametrize("regression", [True, False])
def test_a_regression_fixer_may_fix_the_other_earlier_tests_of_the_module(project, regression):
    sibling = project.root / "conformance" / "login" / "test_login.py"
    sibling.parent.mkdir()
    sibling.write_text("OLD SETUP\n")
    api = ScriptedAgentAPI(
        fixer_turns=[[write(str(sibling), "NEW SETUP\n"), write("app.py", "v1\n")], [submit_fix()]],
        reviewer_turns=[[review("approve")]],
    )
    render_context = make_render_context(project, api, testing_frid="1", frid="2" if regression else "1")

    run_fix(render_context)

    result = api.of("continue", conversation.FIXER_TASK)[0][3][0]["output"]
    [(_, _, _, start_params)] = api.of("start", conversation.FIXER_TASK)
    if regression:
        assert sibling.read_text() == "NEW SETUP\n"
        assert start_params["writable_module_tests_folder"] == str(project.root / "conformance")
    else:
        assert result.startswith("Error: write access denied") and sibling.read_text() == "OLD SETUP\n"
        assert start_params["writable_module_tests_folder"] == ""


def test_failing_tests_of_a_required_module_get_their_siblings_copied_for_the_same_fix(project):
    sibling = project.root / "base_tests" / "health"
    sibling.mkdir(parents=True)
    (sibling / "test_health.py").write_text("SETUP\n")
    api = ScriptedAgentAPI(
        fixer_turns=[[write("app.py", "v1\n")], [submit_fix()]], reviewer_turns=[[review("approve")]]
    )
    render_context = make_render_context(project, api, testing_module="base")
    render_context.conformance_tests.tests_json = {
        "base": {"1": {"folder_name": str(project.tests_folder)}, "2": {"folder_name": str(sibling)}}
    }

    run_fix(render_context)

    assert (project.root / "base_tests" / "health_copy" / "test_health.py").read_text() == "SETUP\n"
    assert os.path.isdir(str(project.tests_folder) + "_copy")


def test_reviewer_last_verdict_counts_and_the_next_review_starts_a_new_session(project):
    api = ScriptedAgentAPI(
        fixer_turns=[[write("app.py", "v1\n")], [submit_fix()], [write("app.py", "v2\n")], [submit_fix()]],
        reviewer_turns=[
            {"status": "tool_calls", "calls": [{"id": "v", **review("reject", "No.")}], "session_ended": True},
            [review("approve")],
        ],
    )
    render_context = make_render_context(project, api)

    run_fix(render_context)

    reviewer_starts = api.of("start", conversation.REVIEWER_TASK)
    assert len(reviewer_starts) == 2 and reviewer_starts[1][3]["previous_session_id"] == reviewer_starts[0][2]


def test_fixer_session_ending_without_a_submission_restarts_both_keeping_the_changes(project):
    api = ScriptedAgentAPI(
        fixer_turns=[[write("app.py", "v1\n")], {"status": "completed", "result": "handoff"}, [submit_fix()]],
        reviewer_turns=[[review("approve")]],
    )
    render_context = make_render_context(project, api)

    outcome, _ = run_fix(render_context)

    assert outcome == FixConformanceTest.IMPLEMENTATION_CODE_UPDATED
    fixer_starts = api.of("start", conversation.FIXER_TASK)
    assert len(fixer_starts) == 2 and fixer_starts[1][3]["previous_session_id"] == fixer_starts[0][2]
    assert (project.build / "app.py").read_text() == "v1\n"


def test_journal_records_the_approved_fix(project):
    api = ScriptedAgentAPI(
        fixer_turns=[[write("app.py", "v1\n")], [submit_fix()]], reviewer_turns=[[review("approve")]]
    )
    render_context = make_render_context(project, api)

    run_fix(render_context)

    [entry] = render_context.memory_manager.journal.build_digest("mod", "1")
    assert entry["hypothesis"] == "off by one" and entry["target"] == "IMPLEMENTATION_CODE"
    assert entry["files_changed"] == ["app.py"]


def test_a_fixer_is_shown_the_fixes_made_for_other_functionalities_tests(project):
    api = ScriptedAgentAPI(
        fixer_turns=[[write("app.py", "v1\n")], [submit_fix()], [write("app.py", "v2\n")], [submit_fix()]],
        reviewer_turns=[[review("approve")], [review("approve")]],
    )
    render_context = make_render_context(project, api, testing_frid="2", frid="2")
    run_fix(render_context)
    assert "other_functionalities_fixes" not in api.of("start", conversation.FIXER_TASK)[0][3]
    ctx = render_context.conformance_tests_running_context
    ctx.current_testing_frid = "1"
    ctx.set_conformance_tests_json("mod", {"1": {"folder_name": str(project.tests_folder)}})

    run_fix(render_context)

    [other_fix] = api.of("start", conversation.FIXER_TASK)[1][3]["other_functionalities_fixes"]
    assert other_fix["functionality"] == qualified_frid("mod", "2") and other_fix["approach"] == "add one"
    assert "+v1" in other_fix["code_diff"]["app.py"]


def test_a_conflict_of_a_functionality_without_acceptance_tests_is_with_the_one_an_earlier_fix_was_for(
    project, monkeypatch
):
    monkeypatch.setattr(
        plain_spec,
        "get_specifications_for_frid",
        lambda tree, frid: ({plain_spec.FUNCTIONAL_REQUIREMENTS: ["- Add numbers."]}, None),
    )
    conflict = submit_fix("CONFLICTING_REQUIREMENTS", conflict_reason="1 and 2 contradict each other.")
    api = ScriptedAgentAPI(fixer_turns=[[conflict]], reviewer_turns=[[review("approve")]])
    render_context = make_render_context(project, api, testing_frid="2", frid="2")
    ctx = render_context.conformance_tests_running_context
    ctx.conflicting_requirement_count = 1
    ctx.conflicting_module_name, ctx.conflicting_frid = "mod", "2"
    ctx.implementation_code_fixes.append({"hypothesis": "h", "approach": "a", "code_diff": {}, "tested": ("mod", "1")})

    with pytest.raises(ConflictingRequirements):
        run_fix(render_context)

    [report] = api.conflict_reports
    assert report[1] == "1" and report[4] == "mod" and report[6] == "CONFLICTING_REQUIREMENTS"


def test_a_test_run_before_any_change_repeats_the_failing_run_instead_of_running_it(project):
    api = ScriptedAgentAPI(
        fixer_turns=[[{"name": "run_conformance_tests", "args": {}}], [write("app.py", "v1\n")], [submit_fix()]],
        reviewer_turns=[[review("approve")]],
    )
    render_context = make_render_context(project, api)

    run_fix(render_context)

    [(_, _, _, results)] = [c for c in api.of("continue", conversation.FIXER_TASK)][:1]
    assert results[0]["output"].startswith("Nothing changed") and "failing run whose output you were given" in (
        results[0]["output"]
    )
