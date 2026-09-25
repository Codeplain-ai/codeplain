"""Tests for the agentic FixUnitTests action driving a scripted fake API."""

from types import SimpleNamespace

import pytest

import plain_spec
from render_machine.actions.fix_unit_tests import MAX_AGENT_TURNS_PER_ATTEMPT, MAX_RELEVANT_FILES_CHARS, FixUnitTests
from render_machine.actions.run_unit_tests import RunUnitTests
from render_machine.render_types import ScriptExecutionHistory, UnitTestsRunningContext


class FakeAPI:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def agent_start(self, task_type, task_params, frid, module_name, run_state):
        self.calls.append(("start", task_type, task_params, frid, module_name))
        return self.responses.pop(0)

    def agent_continue(self, session_id, tool_results, frid, module_name, run_state):
        self.calls.append(("continue", session_id, tool_results, frid, module_name))
        return self.responses.pop(0)


def _tool_calls(*calls):
    return {"session_id": "s1", "status": "tool_calls", "calls": list(calls)}


@pytest.fixture
def render_context(tmp_path, monkeypatch):
    build = tmp_path / "build"
    build.mkdir()
    (build / "a.py").write_text("x = 1\n")
    monkeypatch.chdir(tmp_path)
    plain_source_tree = {"spec": True}
    specifications = {
        plain_spec.DEFINITIONS: ["- :Foo: is a thing."],
        plain_spec.NON_FUNCTIONAL_REQUIREMENTS: ["- Python 3.11."],
        plain_spec.FUNCTIONAL_REQUIREMENTS: ["- Old feature.", "- New feature."],
    }
    monkeypatch.setattr(plain_spec, "get_specifications_for_frid", lambda tree, frid: (specifications, None))
    return SimpleNamespace(
        codeplain_api=None,
        build_folder=str(build),
        module_name="m",
        run_state=object(),
        plain_source_tree=plain_source_tree,
        unittests_script=None,
        test_script_timeout=None,
        stop_event=None,
        frid_context=SimpleNamespace(frid="2", linked_resources={"schema.json": "{}"}, changed_files={"a.py"}),
        unit_tests_running_context=UnitTestsRunningContext(fix_attempts=1),
        script_execution_history=ScriptExecutionHistory(),
        get_required_modules_functionalities=lambda: {"base": ["- Base feature."]},
    )


def test_first_attempt_starts_session_runs_tools_and_stops_at_submit_fix(render_context):
    api = FakeAPI(
        [
            _tool_calls({"id": "c1", "name": "read_file", "args": {"file_path": "a.py"}}),
            _tool_calls(
                {"id": "c2", "name": "edit_file", "args": {"file_path": "a.py", "search": "x = 1", "replace": "x = 2"}},
                {"id": "c3", "name": "submit_fix", "args": {"root_cause": "off by one", "changes_made": "x = 2"}},
            ),
        ]
    )
    render_context.codeplain_api = api

    outcome, payload = FixUnitTests().execute(render_context, {"previous_unittests_issue": "FAILED test_a"})

    assert (outcome, payload) == (FixUnitTests.SUCCESSFUL_OUTCOME, None)
    kind, task_type, task_params, frid, module_name = api.calls[0]
    assert (kind, task_type, frid, module_name) == ("start", "fix_unit_tests", "2", "m")
    assert task_params["unittests_issue"] == "FAILED test_a"
    assert task_params["definitions"] == "- :Foo: is a thing."
    assert task_params["linked_resources"] == {"schema.json": "{}"}
    assert (
        "### Module: base (Already Implemented, for context)\n- Base feature." in task_params["functional_requirements"]
    )
    assert "### Module: m (Already Implemented, for context)\n- Old feature." in task_params["functional_requirements"]
    assert task_params["functional_requirements"].endswith(
        "### Module: m (Currently Being Implemented)\n- New feature."
    )

    # the read_file result went back to the server; the edit was applied locally
    assert api.calls[1][0] == "continue" and api.calls[1][2][0]["call_id"] == "c1"
    assert "1: x = 1" in api.calls[1][2][0]["output"]
    assert open(render_context.build_folder + "/a.py").read() == "x = 2\n"

    context = render_context.unit_tests_running_context
    assert context.agent_session_id == "s1"
    assert context.pending_submit_call_id == "c3"
    assert [r["call_id"] for r in context.pending_tool_results] == ["c2"]
    assert context.changed_files == {"a.py"}


def test_second_attempt_continues_session_answering_submit_fix(render_context):
    context = render_context.unit_tests_running_context
    context.agent_session_id = "s1"
    context.pending_submit_call_id = "c3"
    context.pending_tool_results = [{"call_id": "c2", "output": "Edited"}]
    api = FakeAPI([_tool_calls({"id": "c4", "name": "submit_fix", "args": {"changes_made": "again"}})])
    render_context.codeplain_api = api

    outcome, _ = FixUnitTests().execute(render_context, {"previous_unittests_issue": "FAILED test_b"})

    assert outcome == FixUnitTests.SUCCESSFUL_OUTCOME
    kind, session_id, tool_results, frid, module_name = api.calls[0]
    assert (kind, session_id, frid, module_name) == ("continue", "s1", "2", "m")
    assert tool_results[0] == {"call_id": "c2", "output": "Edited"}
    assert tool_results[1]["call_id"] == "c3" and "still fail" in tool_results[1]["output"]
    assert tool_results[1]["test_output"] == "FAILED test_b"
    assert context.agent_session_id == "s1" and context.pending_submit_call_id == "c4"
    assert context.pending_tool_results == []


@pytest.mark.parametrize(
    "final_response",
    [
        {"session_id": "s1", "status": "completed", "result": "done"},
        {"session_id": "s1", "status": "failed", "error": "x"},
    ],
)
def test_session_ending_without_submission_resets_the_session(render_context, final_response):
    render_context.codeplain_api = FakeAPI([final_response])
    outcome, _ = FixUnitTests().execute(render_context, {"previous_unittests_issue": "FAILED"})
    assert outcome == FixUnitTests.SUCCESSFUL_OUTCOME
    context = render_context.unit_tests_running_context
    assert context.agent_session_id is None and context.pending_submit_call_id is None


def test_turn_cap_per_attempt_resets_the_session(render_context):
    call = {"id": "c", "name": "ls_files", "args": {}}
    render_context.codeplain_api = FakeAPI([_tool_calls(call)] * (MAX_AGENT_TURNS_PER_ATTEMPT + 1))
    FixUnitTests().execute(render_context, {"previous_unittests_issue": "FAILED"})
    assert len(render_context.codeplain_api.calls) == MAX_AGENT_TURNS_PER_ATTEMPT + 1
    assert render_context.unit_tests_running_context.agent_session_id is None


def test_missing_issue_is_an_internal_error(render_context):
    from plain2code_exceptions import InternalClientError

    with pytest.raises(InternalClientError):
        FixUnitTests().execute(render_context, {})


def test_first_turn_is_seeded_with_file_tree_relevant_files_and_log_path(render_context, tmp_path):
    (tmp_path / "build" / "tests").mkdir()
    (tmp_path / "build" / "tests" / "test_a.py").write_text("assert True\n")
    log = tmp_path / "unit.log"
    log.write_text("full log\nCaused by: boom\n")
    render_context.script_execution_history.latest_unit_test_output_path = str(log)
    api = FakeAPI([{"session_id": "s1", "status": "completed", "result": "done"}])
    render_context.codeplain_api = api

    FixUnitTests().execute(render_context, {"previous_unittests_issue": "FAILED"})

    task_params = api.calls[0][2]
    assert task_params["file_tree"].split("\n") == ["a.py", "tests/test_a.py"]
    assert task_params["relevant_files"] == {"a.py": "x = 1\n"}
    assert task_params["unittests_log_path"] == str(log)
    assert "previous_session_id" not in task_params
    # the agent may grep the full log although it is outside the build folder and project root
    assert str(log) in render_context.unit_tests_running_context.readable_log_paths


def test_relevant_files_stay_within_budget(tmp_path):
    (tmp_path / "small.py").write_text("s")
    (tmp_path / "big.py").write_text("b" * MAX_RELEVANT_FILES_CHARS)
    assert FixUnitTests._relevant_files(str(tmp_path), {"small.py", "big.py", "deleted.py"}) == {"small.py": "s"}


def test_new_session_after_abandoned_one_references_it(render_context):
    call = {"id": "c", "name": "ls_files", "args": {}}
    render_context.codeplain_api = FakeAPI([_tool_calls(call)] * (MAX_AGENT_TURNS_PER_ATTEMPT + 1))
    FixUnitTests().execute(render_context, {"previous_unittests_issue": "FAILED"})
    assert render_context.unit_tests_running_context.previous_session_id == "s1"

    api = FakeAPI([{"session_id": "s2", "status": "completed", "result": "done"}])
    render_context.codeplain_api = api
    FixUnitTests().execute(render_context, {"previous_unittests_issue": "FAILED again"})
    assert api.calls[0][0] == "start" and api.calls[0][2]["previous_session_id"] == "s1"


def test_run_unit_tests_action_skips_the_suite_after_a_verified_agent_run(render_context, monkeypatch):
    import render_machine.render_utils as render_utils

    context = render_context.unit_tests_running_context
    context.verified_passing, context.verified_passing_log_path = True, "/logs/pass.log"
    monkeypatch.setattr(render_utils, "execute_script", lambda *a, **k: pytest.fail("suite must not run"))

    assert RunUnitTests().execute(render_context, None) == (RunUnitTests.SUCCESSFUL_OUTCOME, None)
    assert context.verified_passing is False
    assert render_context.script_execution_history.latest_unit_test_output_path == "/logs/pass.log"
