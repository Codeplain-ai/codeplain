"""Tests for the client-side agent tool implementations."""

import os
from types import SimpleNamespace

import pytest

from render_machine import agent_tools
from render_machine.render_context import RenderContext
from render_machine.render_types import UnitTestsRunningContext


class FakeRenderContext(SimpleNamespace):
    unit_tests_agent_session = RenderContext.unit_tests_agent_session

    @property
    def plain_module(self):
        # The rendered modules folder is the build folder's parent, as in plain_modules/<module>.
        return SimpleNamespace(build_folder=os.path.dirname(self.build_folder), all_required_modules=[])


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


def test_read_is_confined_to_build_folder(project):
    outside = str(project.root / "outside.txt")
    assert agent_tools.read_file({"file_path": outside}, project.rc).startswith("Error: read access denied")
    assert agent_tools.read_file({"file_path": "../../outside.txt"}, project.rc).startswith("Error: read access denied")
    assert agent_tools.grep({"pattern": "outside", "file_path": outside}, project.rc).startswith("Error: read access")
    assert agent_tools.ls_files({"pattern": str(project.root)}, project.rc).startswith("Error: read access denied")
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
    # the full log is outside the build folder but greppable
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


def test_grep_takes_python_regex_syntax(project):
    log = project.build / "conformance.script_output"
    log.write_text("Master password: ***\nName: alice\nnoise\nFAILED test_add\nTests run: 12\n")
    out = agent_tools.grep(
        {"pattern": r"Master password|Name:|FAILED|run: \d+", "file_path": str(log)}, project.rc
    ).splitlines()
    assert out == [
        "conformance.script_output:1:Master password: ***",
        "conformance.script_output:2:Name: alice",
        "conformance.script_output:4:FAILED test_add",
        "conformance.script_output:5:Tests run: 12",
    ]
    assert agent_tools.grep({"pattern": "(?i)failed", "file_path": str(log)}, project.rc).endswith("FAILED test_add")
    assert agent_tools.grep({"pattern": "foo(", "file_path": str(log)}, project.rc).startswith(
        "Error: invalid regular expression"
    )


def test_grep_separates_context_groups_and_skips_binary_and_excluded_files(project):
    (project.build / "long.py").write_text("\n".join(["hit"] + ["x"] * 10 + ["hit"]) + "\n")
    (project.build / "blob.bin").write_bytes(b"hit\0\0")
    (project.build / "node_modules").mkdir()
    (project.build / "node_modules" / "dep.py").write_text("hit\n")
    out = agent_tools.grep({"pattern": "^hit$", "context_lines": 1}, project.rc).splitlines()
    assert out == ["long.py:1:hit", "long.py-2-x", "--", "long.py-11-x", "long.py:12:hit"]


def test_edit_names_the_lines_it_removed(project):
    pom = project.build / "pom.xml"
    pom.write_text("<project>\n  <dependencies/>\n</project>\n")
    out = agent_tools.edit_file(
        {
            "file_path": "pom.xml",
            "search": "  <dependencies/>\n</project>\n",
            "replace": "  <dependencies/>\n  <build/>\n",
        },
        project.rc,
    )
    assert "This edit removed these lines: `</project>`" in out
    kept = agent_tools.edit_file(
        {"file_path": "pom.xml", "search": "  <build/>\n", "replace": "  <build/>\n</project>\n"}, project.rc
    )
    assert "removed these lines" not in kept


def test_unit_tests_agent_reads_and_greps_other_modules_but_writes_only_its_build_folder(project):
    other = project.build.parent / "base" / "code" / "lib.py"
    other.parent.mkdir(parents=True)
    other.write_text("def helper():\n    return 1\n")
    assert agent_tools.read_file({"file_path": str(other)}, project.rc).startswith("1: def helper")
    hits = agent_tools.grep({"pattern": "def (helper|sub)", "file_path": str(project.build.parent)}, project.rc)
    assert "base/code/lib.py:1:def helper():" in hits and "def sub(a, b):" in hits
    denied = agent_tools.write_file({"file_path": str(other), "content": "x\n"}, project.rc)
    assert denied.startswith("Error: write access denied") and other.read_text().startswith("def helper")


def test_a_pattern_that_finds_nothing_says_where_to_look(project):
    result = agent_tools.ls_files({"pattern": "**/tests/**/pom.xml"}, project.rc)
    assert result.startswith("No files match")
    assert "Relative paths resolve against" in result and "`tests`" in result


def test_a_search_text_that_is_not_found_gets_the_closest_lines(project):
    test_file = project.build / "ReaderTest.java"
    test_file.write_text('class ReaderTest {\n    Path file = writeCsv("id\\\\n7\\\\n");\n}\n')
    out = agent_tools.edit_file(
        {"file_path": "ReaderTest.java", "search": 'Path file = writeCsv("id\\n7\\n");', "replace": "x"}, project.rc
    )
    assert "found 0 times" in out
    assert '2:     Path file = writeCsv("id\\\\n7\\\\n");' in out
    assert "class ReaderTest" not in out
    unrelated = agent_tools.edit_file(
        {"file_path": "ReaderTest.java", "search": "completely different text here", "replace": "x"}, project.rc
    )
    assert "No line of the file resembles it" in unrelated
