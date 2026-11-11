"""Tests for the agentic generation actions: implementing a functionality and writing its conformance tests, each
driving a scripted fake API."""

import os
from types import SimpleNamespace

import pytest

import file_utils  # noqa: F401  # imported before plain_modules, which it imports back from
import plain_spec
from plain2code_exceptions import FunctionalRequirementTooComplex
from render_machine import agent_session, agent_tools, traceability
from render_machine.actions import render_conformance_tests as conformance_module
from render_machine.actions import render_functional_requirement as implementation_module
from render_machine.actions.render_conformance_tests import RenderConformanceTests
from render_machine.actions.render_functional_requirement import RenderFunctionalRequirement, seeded_files
from render_machine.actions.run_conformance_tests import RunConformanceTests
from render_machine.render_context import RenderContext
from render_machine.render_types import (
    ConformanceTestsRunningContext,
    FridContext,
    ScriptExecutionHistory,
    UnitTestsRunningContext,
)


class FakeAPI:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def agent_start(self, task_type, task_params, frid, module_name, run_state, plain_source_tree=None):
        self.calls.append(("start", task_type, task_params))
        return self.responses.pop(0)

    def agent_continue(self, session_id, tool_results, frid, module_name, run_state):
        self.calls.append(("continue", session_id, tool_results))
        return self.responses.pop(0)

    def check_functional_requirement_breakdown(self, *args, **kwargs):
        self.calls.append(("breakdown",))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _turn(*calls, session_ended=False):
    response = {"session_id": "s1", "status": "tool_calls", "calls": list(calls)}
    if session_ended:
        response["session_ended"] = True
    return response


def _call(call_id, name, **args):
    return {"id": call_id, "name": name, "args": args}


# ---------------------------------------------------------------------------------------------------------------
# agent_session.run_until_submit
# ---------------------------------------------------------------------------------------------------------------


def _session_context(tmp_path, api):
    return SimpleNamespace(
        codeplain_api=api, frid_context=SimpleNamespace(frid="1"), module_name="m", run_state=object()
    )


def test_run_until_submit_executes_calls_and_stops_at_the_submission(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n")
    workspace = agent_tools.AgentWorkspace(build_folder=str(tmp_path), writable_folders=[str(tmp_path)])
    api = FakeAPI([_turn(_call("c2", "read_file", file_path="a.py"), _call("c3", "submit_fix", root_cause="r"))])

    run = agent_session.run_until_submit(
        _session_context(tmp_path, api),
        "s1",
        _turn(_call("c1", "ls_files")),
        "submit_fix",
        workspace,
        max_turns=5,
    )

    assert run.status == "submitted" and run.submit_call["id"] == "c3"
    assert [result["call_id"] for result in run.tool_results] == ["c2"]
    assert "x = 1" in run.tool_results[0]["output"]
    assert run.turns == 2 and [call[0] for call in api.calls] == ["continue"]


def test_run_until_submit_reports_a_run_out_of_turns(tmp_path):
    workspace = agent_tools.AgentWorkspace(build_folder=str(tmp_path), writable_folders=[str(tmp_path)])
    api = FakeAPI([_turn(_call("c2", "ls_files"))])

    run = agent_session.run_until_submit(
        _session_context(tmp_path, api), "s1", _turn(_call("c1", "ls_files")), "submit_fix", workspace, max_turns=1
    )

    assert run.status == "out_of_turns" and run.submit_call is None


# ---------------------------------------------------------------------------------------------------------------
# Implementing a functionality
# ---------------------------------------------------------------------------------------------------------------


class FakeRenderContext(SimpleNamespace):
    start_unittests_processing = RenderContext.start_unittests_processing

    @property
    def plain_module(self):
        return SimpleNamespace(
            build_folder=os.path.dirname(self.build_folder),
            all_required_modules=[],
            revert_code_to_frid=lambda frid: None,
        )

    def should_run_unit_tests(self):
        return self.unittests_script is not None

    def functionality_label(self, frid=None):
        return frid or self.frid_context.frid

    def current_functionality_position(self):
        return None


@pytest.fixture
def implementation_context(tmp_path, monkeypatch):
    build = tmp_path / "plain_modules" / "m" / "code"
    build.mkdir(parents=True)
    (build / "app.py").write_text("def main(): pass\n")
    script = tmp_path / "run_unittests.sh"
    script.write_text("pytest $1\n")
    specifications = {
        plain_spec.DEFINITIONS: ["- :Task: is a to-do item."],
        plain_spec.NON_FUNCTIONAL_REQUIREMENTS: ["- Python."],
        plain_spec.FUNCTIONAL_REQUIREMENTS: ["- Old feature.", "- Add a :Task:."],
    }
    monkeypatch.setattr(plain_spec, "get_specifications_for_frid", lambda tree, frid: (specifications, None))
    monkeypatch.setattr(implementation_module.render_utils, "print_inputs", lambda *args: None)
    monkeypatch.setattr(implementation_module.render_utils, "revert_changes_for_frid", lambda *args: None)
    return FakeRenderContext(
        codeplain_api=None,
        build_folder=str(build),
        module_name="m",
        run_state=SimpleNamespace(increment_unittest_batch_id=lambda: None),
        plain_source_tree={"tree": True},
        unittests_script=str(script),
        test_script_timeout=None,
        stop_event=None,
        frid_context=FridContext(
            frid="add-task",
            specifications=specifications,
            functional_requirement_text="- Add a :Task:.",
            linked_resources={},
        ),
        unit_tests_running_context=None,
        conformance_tests_running_context=None,
        script_execution_history=ScriptExecutionHistory(),
        memory_manager=SimpleNamespace(memory_folder=str(tmp_path / "memory")),
        get_required_modules_functionalities=lambda: {},
    )


def test_implementer_writes_files_runs_unit_tests_and_hands_over_the_passing_run(implementation_context, monkeypatch):
    monkeypatch.setattr(agent_tools.render_utils, "execute_script", lambda *args, **kwargs: (0, "ok", "/logs/unit"))
    api = FakeAPI(
        [
            _turn(
                _call("c1", "write_file", file_path="tasks.py", content="def add(): ...\n"),
                _call("c2", "edit_file", file_path="app.py", search="pass", replace="return add()"),
            ),
            _turn(_call("c3", "run_unit_tests")),
            _turn(_call("c4", "submit_implementation", summary="Added add().", files_modified=["tasks.py"])),
        ]
    )
    implementation_context.codeplain_api = api

    outcome, _ = RenderFunctionalRequirement().execute(implementation_context, None)

    assert outcome == RenderFunctionalRequirement.SUCCESSFUL_OUTCOME
    start = api.calls[0]
    assert start[1] == "implement_functionality"
    assert start[2]["existing_files"] == {"app.py": "def main(): pass\n"} and start[2]["existing_files_complete"]
    assert start[2]["functionality"] == "- Add a :Task:." and start[2]["unittests_script_content"] == "pytest $1\n"
    assert not start[2]["is_new_project"]
    build = implementation_context.build_folder
    assert open(os.path.join(build, "tasks.py")).read() == "def add(): ...\n"
    assert implementation_context.frid_context.changed_files == {"tasks.py", "app.py"}
    assert implementation_context.frid_context.implementation_unit_tests_passed

    # The unit-test loop that follows accepts the implementer's passing run, once.
    implementation_context.start_unittests_processing()
    context = implementation_context.unit_tests_running_context
    assert context.verified_passing and context.verified_passing_log_path == "/logs/unit"
    assert not implementation_context.frid_context.implementation_unit_tests_passed


def test_an_edit_after_the_passing_run_makes_the_unit_tests_run_again(implementation_context, monkeypatch):
    monkeypatch.setattr(agent_tools.render_utils, "execute_script", lambda *args, **kwargs: (0, "ok", "/logs/unit"))
    api = FakeAPI(
        [
            _turn(_call("c1", "run_unit_tests")),
            _turn(_call("c2", "write_file", file_path="late.py", content="x = 1\n")),
            _turn(_call("c3", "submit_implementation", summary="Done.", files_modified=["late.py"])),
        ]
    )
    implementation_context.codeplain_api = api

    RenderFunctionalRequirement().execute(implementation_context, None)

    assert not implementation_context.frid_context.implementation_unit_tests_passed


def test_without_a_unit_tests_script_the_implementer_cannot_run_them(implementation_context):
    implementation_context.unittests_script = None
    api = FakeAPI(
        [
            _turn(_call("c1", "run_unit_tests")),
            _turn(_call("c2", "submit_implementation", summary="Done.", files_modified=[])),
        ]
    )
    implementation_context.codeplain_api = api

    RenderFunctionalRequirement().execute(implementation_context, None)

    assert api.calls[0][1] == "implement_functionality_without_unit_tests"
    assert "unittests_script_content" not in api.calls[0][2]
    assert "unknown tool" in api.calls[1][2][0]["output"]


def test_the_render_cache_answer_is_written_without_a_session(implementation_context):
    api = FakeAPI([{"status": "completed", "session_id": None, "cached_files": {"cached.py": "y = 2\n"}}])
    implementation_context.codeplain_api = api

    outcome, _ = RenderFunctionalRequirement().execute(implementation_context, None)

    assert outcome == RenderFunctionalRequirement.SUCCESSFUL_OUTCOME
    assert open(os.path.join(implementation_context.build_folder, "cached.py")).read() == "y = 2\n"
    assert implementation_context.frid_context.changed_files == {"cached.py"}


def test_a_functionality_judged_too_complex_is_broken_down_when_feasible(implementation_context):
    api = FakeAPI(
        [
            _turn(
                _call(
                    "c1",
                    "submit_implementation",
                    summary="Partial.",
                    files_modified=[],
                    functionality_too_complex=True,
                )
            ),
            FunctionalRequirementTooComplex("too complex", {"functional_requirements": {"1": "- Part one."}}),
        ]
    )
    implementation_context.codeplain_api = api

    outcome, payload = RenderFunctionalRequirement().execute(implementation_context, None)

    assert outcome == RenderFunctionalRequirement.FUNCTIONAL_REQUIREMENT_TOO_COMPLEX_OUTCOME
    assert "- Part one." in str(payload)


def test_a_functionality_judged_too_complex_is_accepted_when_no_breakdown_is_feasible(implementation_context):
    api = FakeAPI(
        [
            _turn(
                _call("c1", "write_file", file_path="b.py", content="z = 3\n"),
            ),
            _turn(
                _call(
                    "c2",
                    "submit_implementation",
                    summary="All.",
                    files_modified=["b.py"],
                    functionality_too_complex=True,
                )
            ),
            {"breakdown_feasible": False},
        ]
    )
    implementation_context.codeplain_api = api

    outcome, _ = RenderFunctionalRequirement().execute(implementation_context, None)

    assert outcome == RenderFunctionalRequirement.SUCCESSFUL_OUTCOME
    assert api.calls[-1] == ("breakdown",)


def test_a_session_that_wrote_nothing_and_did_not_submit_fails_the_functionality(implementation_context):
    api = FakeAPI([{"status": "failed", "session_id": "s1", "error": "LLM down"}])
    implementation_context.codeplain_api = api

    with pytest.raises(implementation_module.ImplementingAgentFailed):
        RenderFunctionalRequirement().execute(implementation_context, None)


def test_seeded_files_are_the_whole_folder_when_it_fits_and_the_most_recent_otherwise(tmp_path, monkeypatch):
    contents = {"old.py": "a" * 10, "new.py": "b" * 10}
    assert seeded_files(str(tmp_path), contents) == (contents and dict(sorted(contents.items())), True)

    monkeypatch.setattr(implementation_module, "MAX_SEEDED_FILES_CHARS", 15)
    (tmp_path / "old.py").write_text("a" * 10)
    (tmp_path / "new.py").write_text("b" * 10)
    os.utime(tmp_path / "old.py", (1, 1))
    assert seeded_files(str(tmp_path), contents) == ({"new.py": "b" * 10}, False)


# ---------------------------------------------------------------------------------------------------------------
# Writing conformance tests
# ---------------------------------------------------------------------------------------------------------------


class FakeConformanceContext(SimpleNamespace):
    @property
    def plain_module(self):
        return SimpleNamespace(
            build_folder=os.path.dirname(os.path.dirname(self.build_folder)), all_required_modules=[]
        )

    def testing_functionality_label(self):
        return "add-task"


SCENARIOS = [{"id": "add-1", "text": "Adding a task lists it."}, {"id": "add-2", "text": "An empty task fails."}]


@pytest.fixture
def conformance_context(tmp_path, monkeypatch):
    build = tmp_path / "plain_modules" / "m" / "code"
    build.mkdir(parents=True)
    (build / "app.py").write_text("def add(): ...\n")
    tests_root = tmp_path / "plain_modules" / "m" / "tests"
    tests_root.mkdir()
    script = tmp_path / "run_conformance.sh"
    script.write_text("pytest $2\n")
    specifications = {
        plain_spec.DEFINITIONS: ["- :Task: is a to-do item."],
        plain_spec.TEST_REQUIREMENTS: ["- :ConformanceTests: use pytest."],
        plain_spec.FUNCTIONAL_REQUIREMENTS: ["- Add a :Task:."],
        plain_spec.CONFORMANCE_TESTS: SCENARIOS,
    }
    monkeypatch.setattr(plain_spec, "get_specifications_for_frid", lambda tree, frid: (specifications, None))
    monkeypatch.setattr(conformance_module.prepare_testing_environment, "start_in_background", lambda ctx: None)
    monkeypatch.setattr(conformance_module, "_functionality_code_diff", lambda ctx: {"app.py": "+def add(): ..."})
    ctx = ConformanceTestsRunningContext(
        current_testing_module_name="m",
        current_testing_frid="add-task",
        fix_attempts=0,
        conformance_tests_json={"list-tasks": {"folder_name": str(tests_root / "list_tasks")}},
        conformance_tests_render_attempts=0,
        current_testing_frid_specifications=specifications,
        should_prepare_testing_environment=False,
        frid_being_implemented="add-task",
    )
    (tests_root / "list_tasks").mkdir()
    (tests_root / "list_tasks" / "test_list.py").write_text("def test_list_1(): ...\n")
    api = FakeAPI([])
    api.generate_folder_name_from_functional_requirement = lambda **kwargs: "add_task"
    conformance_tests = SimpleNamespace(
        fetch_existing_conformance_test_folder_names=lambda module: ["list_tasks"],
        get_module_conformance_tests_folder=lambda module: str(tests_root),
    )
    return FakeConformanceContext(
        codeplain_api=api,
        build_folder=str(build),
        module_name="m",
        run_state=object(),
        plain_source_tree={},
        required_modules=[],
        conformance_tests_script=str(script),
        prepare_environment_script=None,
        test_script_timeout=None,
        stop_event=None,
        conformance_tests=conformance_tests,
        conformance_tests_running_context=ctx,
        memory_manager=SimpleNamespace(memory_folder=str(tmp_path / "memory")),
        script_execution_history=ScriptExecutionHistory(),
        frid_context=SimpleNamespace(
            frid="add-task", linked_resources={}, specifications=specifications, changed_files={"app.py"}
        ),
        get_required_modules_functionalities=lambda: {},
    )


TEST_FILE = "def test_add_1(): ...\n\ndef test_add_2(): ...\n"


def test_writer_is_seeded_writes_only_tests_and_gets_wrong_tracing_back(conformance_context, monkeypatch):
    tests_folder = os.path.join(
        conformance_context.conformance_tests.get_module_conformance_tests_folder("m"), "add_task"
    )
    monkeypatch.setattr(agent_tools.render_utils, "execute_script", lambda *args, **kwargs: (0, "passed", None))
    wrong = [{"test_name": "test_add_1", "file_name": "test_add.py", "scenario_id": "add-1"}]
    right = wrong + [{"test_name": "test_add_2", "file_name": "test_add.py", "scenario_id": "add-2"}]
    conformance_context.codeplain_api.responses = [
        _turn(
            _call("c1", "write_file", file_path=os.path.join(tests_folder, "test_add.py"), content=TEST_FILE),
            _call("c2", "write_file", file_path="app.py", content="hacked\n"),
        ),
        _turn(_call("c3", "run_conformance_tests")),
        _turn(_call("c4", "submit_conformance_tests", summary="Two tests.", tests=wrong)),
        _turn(_call("c5", "submit_conformance_tests", summary="Two tests.", tests=right)),
    ]

    outcome, _ = RenderConformanceTests().execute(conformance_context, None)

    assert outcome == RenderConformanceTests.SUCCESSFUL_OUTCOME
    api = conformance_context.codeplain_api
    start = api.calls[0]
    assert start[1] == "write_conformance_tests"
    assert "`add-1`" in start[2]["conformance_scenarios"] and start[2]["conformance_tests_folder"] == tests_folder
    assert start[2]["example_tests_files"] == {
        os.path.join(os.path.dirname(tests_folder), "list_tasks", "test_list.py"): "def test_list_1(): ...\n"
    }
    assert start[2]["code_diff"] == {"app.py": "+def add(): ..."}
    # The implementation cannot be changed.
    write_results = api.calls[1][2]
    assert "write access denied" in write_results[1]["output"]
    assert open(os.path.join(conformance_context.build_folder, "app.py")).read() == "def add(): ...\n"
    # The submission missing add-2 was sent back with what is wrong.
    correction = api.calls[3][2][-1]
    assert correction["call_id"] == "c4" and "add-2" in correction["output"]
    # The writer's passing run lets the harness skip its own.
    ctx = conformance_context.conformance_tests_running_context
    assert ctx.verified_passing_run is not None
    assert ctx.get_current_conformance_test_folder_name().endswith("add_task")


def test_the_harness_skips_a_run_the_writer_already_passed_on_the_same_files(conformance_context, monkeypatch):
    ctx = conformance_context.conformance_tests_running_context
    tests_folder = os.path.join(conformance_context.conformance_tests.get_module_conformance_tests_folder("m"), "t")
    os.makedirs(tests_folder)
    ctx.get_conformance_tests_json("m")["add-task"] = {"folder_name": tests_folder}
    conformance_context.memory_manager = SimpleNamespace(journal=SimpleNamespace(record_result=lambda *a, **k: None))
    conformance_context.testing_functionality_position = lambda: None
    ctx.conformance_fix_agents = None
    runs = []
    monkeypatch.setattr(
        "render_machine.actions.run_conformance_tests.render_utils.execute_script",
        lambda *args, **kwargs: runs.append(args) or (0, "", None),
    )

    ctx.verified_passing_run = (agent_tools.test_run_files(conformance_context, tests_folder), "/logs/c")
    assert RunConformanceTests().execute(conformance_context, None)[0] == RunConformanceTests.SUCCESSFUL_OUTCOME
    assert runs == [] and ctx.verified_passing_run is None

    # A change since the writer's run (or no verified run at all) runs the tests.
    ctx.verified_passing_run = (agent_tools.test_run_files(conformance_context, tests_folder), None)
    with open(os.path.join(tests_folder, "test_new.py"), "w") as f:
        f.write("x = 1\n")
    RunConformanceTests().execute(conformance_context, None)
    assert len(runs) == 1


def test_traceability_reports_missing_duplicated_and_unfound_tests(tmp_path):
    files = {"test_a.py": "def test_one(): ...\ndef test_two(): ..."}
    tests = [
        {"test_name": "test_one", "file_name": "test_a.py", "scenario_id": "s1"},
        {"test_name": "test_two", "file_name": str(tmp_path / "test_a.py"), "scenario_id": "s1"},
        {"test_name": "test_ghost", "file_name": "test_a.py", "scenario_id": "s2"},
    ]
    traced = traceability.check_traceability(["s1", "s2"], tests, files, str(tmp_path))

    assert traced["duplicated"] == ["s1"] and traced["missing"] == ["s2"]
    assert traced["not_found"] == ["test_ghost (test_a.py)"]
    problems = traceability.describe_problems(traced)
    assert "s2" in problems and "test_ghost" in problems
    assert (
        traceability.describe_problems(traceability.check_traceability(["s1"], tests[:1], files, str(tmp_path))) == ""
    )
