"""Tests for the client-side agent tool implementations."""

import os
from types import SimpleNamespace

import pytest

from render_machine import agent_tools


@pytest.fixture
def project(tmp_path, monkeypatch):
    build = tmp_path / "plain_modules" / "m"
    build.mkdir(parents=True)
    (build / "app.py").write_text("def add(a, b):\n    return a - b\n\n\ndef sub(a, b):\n    return a - b\n")
    (tmp_path / "outside.txt").write_text("outside\n")
    monkeypatch.chdir(tmp_path)
    render_context = SimpleNamespace(
        build_folder=str(build),
        unit_tests_running_context=SimpleNamespace(changed_files=set()),
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
    outcomes = iter([(0, "", None), (1, "FAILED test_x", "/tmp/log.txt")])
    monkeypatch.setattr(agent_tools.render_utils, "execute_script", lambda *a, **k: next(outcomes))
    project.rc.unittests_script = "run_tests.sh"
    assert agent_tools.run_unit_tests({}, project.rc) == "All unit tests passed."
    failure = agent_tools.run_unit_tests({}, project.rc)
    assert (
        failure.startswith("Unit tests failed (exit code 1). Full output: /tmp/log.txt") and "FAILED test_x" in failure
    )


def test_bound_truncates_long_lines_and_large_output():
    bounded = agent_tools._bound("a" * (agent_tools.MAX_LINE_CHARS + 5) + "\n" + "b\n" * 40_000)
    assert "[line truncated]" in bounded and "[truncated" in bounded
    assert len(bounded) < agent_tools.MAX_OUTPUT_CHARS + 200
