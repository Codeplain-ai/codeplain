"""Tests for the client-side agent tool implementations."""

import os
from types import SimpleNamespace

import pytest

from render_machine import agent_tools
from render_machine.render_context import RenderContext
from render_machine.render_types import UnitTestsRunningContext


class FakeRenderContext(SimpleNamespace):
    unit_tests_agent_session = RenderContext.unit_tests_agent_session


@pytest.fixture
def project(tmp_path, monkeypatch):
    build = tmp_path / "plain_modules" / "m"
    build.mkdir(parents=True)
    (build / "app.py").write_text("def add(a, b):\n    return a - b\n\n\ndef sub(a, b):\n    return a - b\n")
    (tmp_path / "outside.txt").write_text("outside\n")
    monkeypatch.chdir(tmp_path)
    render_context = FakeRenderContext(
        build_folder=str(build),
        unit_tests_running_context=UnitTestsRunningContext(fix_attempts=1),
        conformance_tests_running_context=None,
        unittests_script=None,
        test_script_timeout=None,
        stop_event=None,
    )
    return SimpleNamespace(root=tmp_path, build=build, rc=render_context)


def test_read_file_resolves_relative_to_build_folder_with_paging(project):
    out = agent_tools.read_file({"file_path": "app.py", "offset": 2, "limit": 1}, project.rc)
    assert out.startswith("2:     return a - b")
    assert "use offset=3 to continue" in out


def test_read_allows_project_root_but_not_elsewhere(project):
    assert "outside" in agent_tools.read_file({"file_path": str(project.root / "outside.txt")}, project.rc)
    assert agent_tools.read_file({"file_path": "/etc/hosts"}, project.rc).startswith("Error: read access denied")
    assert agent_tools.read_file({"file_path": "missing.py"}, project.rc).startswith("Error: file not found")


def test_edit_file_requires_unique_match_and_tracks_change(project):
    ambiguous = agent_tools.edit_file({"file_path": "app.py", "search": "return a - b", "replace": "x"}, project.rc)
    assert "found 2 times" in ambiguous
    assert project.rc.unit_tests_running_context.changed_files == set()

    ok = agent_tools.edit_file(
        {
            "file_path": "app.py",
            "search": "def add(a, b):\n    return a - b",
            "replace": "def add(a, b):\n    return a + b",
        },
        project.rc,
    )
    assert ok.startswith("Edited")
    assert "return a + b" in (project.build / "app.py").read_text()
    assert project.rc.unit_tests_running_context.changed_files == {"app.py"}


def test_write_and_delete_are_confined_to_build_folder(project):
    denied = agent_tools.write_file({"file_path": str(project.root / "evil.py"), "content": "x"}, project.rc)
    assert denied.startswith("Error: write access denied")
    assert not (project.root / "evil.py").exists()

    assert agent_tools.write_file({"file_path": "pkg/new.py", "content": "print(1)\n"}, project.rc).startswith("Wrote")
    assert (project.build / "pkg" / "new.py").read_text() == "print(1)\n"
    assert agent_tools.delete_file({"file_path": "pkg/new.py"}, project.rc).startswith("Deleted")
    assert not (project.build / "pkg" / "new.py").exists()
    assert project.rc.unit_tests_running_context.changed_files == {os.path.join("pkg", "new.py")}


def test_grep_and_ls(project):
    hits = agent_tools.grep({"pattern": "def sub"}, project.rc)
    assert hits == "app.py:5:def sub(a, b):"
    assert agent_tools.grep({"pattern": "nope"}, project.rc).startswith("No matches")
    assert agent_tools.grep({"pattern": ""}, project.rc).startswith("Error")

    assert agent_tools.ls_files({}, project.rc).splitlines()[1:] == ["app.py"]
    assert agent_tools.ls_files({"pattern": "**/*.py"}, project.rc).endswith("app.py")


def test_execute_calls_answers_every_call_and_captures_errors(project, monkeypatch):
    def boom(_args, _rc):
        raise RuntimeError("kaput")

    monkeypatch.setitem(agent_tools.TOOLS, "boom", boom)
    results = agent_tools.execute_calls(
        [
            {"id": "1", "name": "read_file", "args": {"file_path": "app.py", "limit": 1}},
            {"id": "2", "name": "unknown_tool", "args": {}},
            {"id": "3", "name": "boom"},
        ],
        project.rc,
    )
    assert [r["call_id"] for r in results] == ["1", "2", "3"]
    assert results[0]["output"].startswith("1: def add")
    assert results[1]["output"] == "Error: unknown tool 'unknown_tool'."
    assert results[2]["output"] == "Error: tool 'boom' failed: RuntimeError: kaput"


def test_run_unit_tests_reports_pass_and_failure(project, monkeypatch):
    log = project.root.parent / "log.txt"
    log.write_text("FAILED test_x\nCaused by: boom\n")
    outcomes = iter([(0, "", "/tmp/pass.txt"), (1, "FAILED test_x", str(log))])
    monkeypatch.setattr(agent_tools.render_utils, "execute_script", lambda *a, **k: next(outcomes))
    project.rc.unittests_script = "run_tests.sh"
    context = project.rc.unit_tests_running_context

    assert agent_tools.run_unit_tests({}, project.rc) == {"output": "All unit tests passed."}
    assert context.verified_passing and context.verified_passing_log_path == "/tmp/pass.txt"

    failure = agent_tools.run_unit_tests({}, project.rc)
    assert failure["output"].startswith(f"Unit tests failed (exit code 1). Full log: {log}")
    # raw output goes to the server for condensing, not truncated here
    assert failure["test_output"] == "FAILED test_x"
    # the full log is outside the build folder and project root but greppable
    assert agent_tools.grep({"pattern": "Caused by", "file_path": str(log)}, project.rc).endswith("Caused by: boom")


def test_file_change_invalidates_verified_pass_and_read_cache(project):
    context = project.rc.unit_tests_running_context
    context.verified_passing = True
    first = agent_tools.execute_calls([{"id": "1", "name": "read_file", "args": {"file_path": "app.py"}}], project.rc)
    repeat = agent_tools.execute_calls([{"id": "2", "name": "read_file", "args": {"file_path": "app.py"}}], project.rc)
    assert first[0]["output"].startswith("1: def add")
    assert repeat[0]["output"].startswith("Same call as an earlier one")

    agent_tools.write_file({"file_path": "other.py", "content": "y = 1\n"}, project.rc)
    assert context.verified_passing is False
    again = agent_tools.execute_calls([{"id": "3", "name": "read_file", "args": {"file_path": "app.py"}}], project.rc)
    assert again[0]["output"].startswith("1: def add")


def test_edit_file_returns_the_edited_region(project):
    out = agent_tools.edit_file(
        {
            "file_path": "app.py",
            "search": "def sub(a, b):\n    return a - b",
            "replace": "def sub(a, b):\n    return b",
        },
        project.rc,
    )
    assert out.startswith("Edited") and "5: def sub(a, b):\n6:     return b" in out
    assert "2:     return a - b" in out and "1: def add" not in out


def test_grep_context_lines_and_include(project):
    (project.build / "notes.txt").write_text("def sub is documented here\n")
    hits = agent_tools.grep({"pattern": "def sub", "context_lines": 1, "include": "*.py"}, project.rc)
    assert "notes.txt" not in hits
    assert "app.py-4-" in hits and "app.py:5:def sub(a, b):" in hits and "app.py-6-" in hits


def test_bound_truncates_long_lines_and_large_output():
    bounded = agent_tools._bound("a" * (agent_tools.MAX_LINE_CHARS + 5) + "\n" + "b\n" * 40_000)
    assert "[line truncated]" in bounded and "[truncated" in bounded
    assert len(bounded) < agent_tools.MAX_OUTPUT_CHARS + 200
