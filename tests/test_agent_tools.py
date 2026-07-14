"""Tests for the agent tool implementations and their contract with the server.

The tool *descriptions* the model sees live in the server repository
(src/agent/tools.py) while the *implementations* live here. These tests pin the
behavioral claims the descriptions make (so the model is never lied to about what a
tool did) and, when the server repository is checked out as a sibling, verify the
two tool sets have not drifted apart.
"""

import os
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from render_machine.agent import tools
from render_machine.agent.agent_runner import TERMINAL_TOOLS
from render_machine.agent.tool_executor import DEFAULT_TOOLS


def _fake_render_context(build_folder: str) -> SimpleNamespace:
    """Minimal render-context stand-in accepted by the file tools."""
    return SimpleNamespace(
        build_folder=build_folder,
        conformance_tests_running_context=None,
        conformance_tests_folder=None,
        memory_manager=None,
        conformance_tests_script=None,
        prepare_environment_script=None,
        unittests_script=None,
        frid_context=None,
        stop_event=None,
    )


@pytest.fixture
def project_dir():
    with tempfile.TemporaryDirectory() as temp_dir:
        yield temp_dir


def test_read_file_default_reads_from_beginning(project_dir):
    """The tool description promises: "If not provided, reads from the beginning".

    Regression test: read_file used to return the LAST 200 lines by default, so the
    model saw the tail of a source file while believing it had read the top.
    """
    build_folder = os.path.join(project_dir, "build")
    os.makedirs(build_folder)
    file_path = os.path.join(build_folder, "big.py")
    Path(file_path).write_text("".join(f"# line_{i:03d}\n" for i in range(1, 301)), encoding="utf-8")

    result = tools.read_file({"file_path": file_path}, _fake_render_context(build_folder))

    assert "line_001" in result
    assert "line_200" in result
    assert "line_201" not in result
    assert "Showing lines 1-200 of 300" in result


def test_read_file_offset_and_limit(project_dir):
    build_folder = os.path.join(project_dir, "build")
    os.makedirs(build_folder)
    file_path = os.path.join(build_folder, "big.py")
    Path(file_path).write_text("".join(f"# line_{i:03d}\n" for i in range(1, 301)), encoding="utf-8")

    result = tools.read_file({"file_path": file_path, "offset": 250, "limit": 10}, _fake_render_context(build_folder))

    assert "line_250" in result
    assert "line_259" in result
    assert "line_249" not in result
    assert "line_260" not in result


def test_grep_finds_code_in_build_folder_named_build(project_dir):
    """Regression test: grep hard-excluded directories named "build" and "dist", so a
    search from the project root silently skipped the entire implementation whenever
    the build folder had its default name ("build")."""
    if subprocess.run(["which", "grep"], capture_output=True).returncode != 0:
        pytest.skip("grep not available")

    build_folder = os.path.join(project_dir, "build")
    os.makedirs(build_folder)
    Path(os.path.join(build_folder, "foo.py")).write_text("needle_xyz = 1\n", encoding="utf-8")

    result = tools.grep({"pattern": "needle_xyz", "file_path": project_dir}, _fake_render_context(build_folder))

    assert "foo.py" in result
    assert "No matches found" not in result


def test_grep_still_excludes_artifact_dirs_when_not_project_folders(project_dir):
    """When no project folder is named "build"/"dist", they stay excluded as artifacts."""
    if subprocess.run(["which", "grep"], capture_output=True).returncode != 0:
        pytest.skip("grep not available")

    build_folder = os.path.join(project_dir, "output")
    artifact_folder = os.path.join(project_dir, "dist")
    os.makedirs(build_folder)
    os.makedirs(artifact_folder)
    Path(os.path.join(artifact_folder, "bundle.js")).write_text("needle_xyz = 1\n", encoding="utf-8")

    result = tools.grep({"pattern": "needle_xyz", "file_path": project_dir}, _fake_render_context(build_folder))

    assert "No matches found" in result


def test_edit_file_multiple_matches_lists_locations(project_dir):
    build_folder = os.path.join(project_dir, "build")
    os.makedirs(build_folder)
    file_path = os.path.join(build_folder, "app.py")
    Path(file_path).write_text("def alpha():\n    return 1\n\ndef beta():\n    return 1\n", encoding="utf-8")

    result = tools.edit_file(
        {"file_path": file_path, "search": "    return 1", "replace": "    return 2"},
        _fake_render_context(build_folder),
    )

    assert result.startswith("Error: Search text found 2 times")
    assert "line 2" in result
    assert "line 5" in result
    assert "def alpha():" in result
    assert "def beta():" in result


def test_edit_file_fuzzy_miss_shows_closest_section(project_dir):
    build_folder = os.path.join(project_dir, "build")
    os.makedirs(build_folder)
    file_path = os.path.join(build_folder, "app.py")
    file_lines = [
        "def compute_total(items):",
        "    total = 0",
        "    for item in items:",
        "        total += item.price * item.quantity",
        "    return total",
    ]
    Path(file_path).write_text("\n".join(file_lines) + "\n", encoding="utf-8")

    # Two of five lines differ from the file -> below the 90% threshold, but close
    # enough that a best-match window exists.
    search_lines = [
        "def compute_total(items):",
        "    total = 0.0",
        "    for item in items:",
        "        total += item.price * item.qty",
        "    return total",
    ]
    result = tools.edit_file(
        {"file_path": file_path, "search": "\n".join(search_lines), "replace": "x"},
        _fake_render_context(build_folder),
    )

    assert result.startswith("Error: Search text not found")
    # The closest section is included so the model can correct its search string
    # without a read_file round trip.
    assert "total += item.price * item.quantity" in result


def test_edit_file_fuzzy_match_tolerates_extra_line(project_dir):
    """A search block that is off by one inserted line should still fuzzy-match."""
    build_folder = os.path.join(project_dir, "build")
    os.makedirs(build_folder)
    file_path = os.path.join(build_folder, "app.py")
    file_lines = [
        "def process(data):",
        "    validate(data)",
        "    log_input(data)",  # extra line the search block does not know about
        "    normalized = normalize(data)",
        "    enriched = enrich(normalized)",
        "    result = transform(enriched)",
        "    audit(result)",
        "    return result",
    ]
    Path(file_path).write_text("\n".join(file_lines) + "\n", encoding="utf-8")

    search = "\n".join(line for line in file_lines if line != "    log_input(data)")
    result = tools.edit_file(
        {"file_path": file_path, "search": search, "replace": "def process(data):\n    return transform(data)"},
        _fake_render_context(build_folder),
    )

    assert "Successfully edited" in result
    assert "fuzzy match" in result
    assert "return transform(data)" in Path(file_path).read_text(encoding="utf-8")


def test_get_session_changes_reports_cumulative_diff(project_dir):
    build_folder = os.path.join(project_dir, "build")
    os.makedirs(build_folder)

    modified_path = os.path.join(build_folder, "app.py")
    Path(modified_path).write_text("original_value = 1\n", encoding="utf-8")
    new_path = os.path.join(build_folder, "helper.py")

    render_context = _fake_render_context(build_folder)
    render_context.conformance_tests_running_context = SimpleNamespace(
        file_change_tracker={modified_path: "original_value = 1\n", new_path: None}
    )

    Path(modified_path).write_text("original_value = 2\n", encoding="utf-8")
    Path(new_path).write_text("def helper():\n    return 42\n", encoding="utf-8")

    result = tools.get_session_changes({}, render_context)

    assert "-original_value = 1" in result
    assert "+original_value = 2" in result
    assert "(new file)" in result
    assert "def helper():" in result


def test_get_session_changes_without_tracked_changes(project_dir):
    build_folder = os.path.join(project_dir, "build")
    os.makedirs(build_folder)
    render_context = _fake_render_context(build_folder)
    render_context.conformance_tests_running_context = SimpleNamespace(file_change_tracker={})

    result = tools.get_session_changes({}, render_context)

    assert "No changes have been made" in result


def test_bound_inline_output_caps_single_huge_line():
    """Line-count caps alone do not bound size — one line can be megabytes."""
    output = "x" * 2_000_000  # one 2MB line, well under any line-count cap

    bounded, note = tools._bound_inline_output(output)

    assert len(bounded) <= tools.MAX_INLINE_OUTPUT_CHARS + 200  # + truncation notices
    assert note
    assert "line truncated" in bounded


def test_bound_inline_output_caps_many_capped_lines_by_total_budget():
    """200 lines each under the per-line cap can still blow the total budget."""
    output = "\n".join("y" * 9_000 for _ in range(150))  # ~1.35MB across 150 lines

    bounded, note = tools._bound_inline_output(output)

    assert len(bounded) <= tools.MAX_INLINE_OUTPUT_CHARS + 200
    assert "capped at" in note


def test_bound_inline_output_head_tail_and_untouched_small_output():
    many_lines = "\n".join(f"line {i}" for i in range(1, 1001))
    bounded, note = tools._bound_inline_output(many_lines)
    assert "line 1" in bounded and "line 1000" in bounded
    assert "line 500" not in bounded
    assert "omitted" in bounded and note

    small = "hello\nworld"
    bounded, note = tools._bound_inline_output(small)
    assert bounded == small and note == ""


def test_bound_inline_output_zero_tail_keeps_head_only():
    output = "\n".join(f"match {i}" for i in range(1, 301))
    bounded, note = tools._bound_inline_output(output, head_lines=100, tail_lines=0)
    assert "match 100" in bounded
    assert "match 250" not in bounded and "match 300" not in bounded
    assert note


def test_run_command_bounds_pathological_output(project_dir):
    """A command emitting one enormous line must not flood the tool result."""
    build_folder = os.path.join(project_dir, "build")
    os.makedirs(build_folder)

    result = tools.run_command(
        {"command": "head -c 2000000 /dev/zero | tr '\\0' 'x'"},
        _fake_render_context(build_folder),
    )

    assert len(result) <= tools.MAX_INLINE_OUTPUT_CHARS + 1_000  # + header/notices/path
    assert "Output truncated" in result
    assert "saved at:" in result  # full output remains reachable via read_file


def test_grep_bounds_match_on_minified_line(project_dir):
    build_folder = os.path.join(project_dir, "build")
    os.makedirs(build_folder)
    minified = "var a=1;" * 100_000 + "needle_xyz" + ";var b=2;" * 100_000
    Path(os.path.join(build_folder, "bundle.js")).write_text(minified, encoding="utf-8")

    result = tools.grep({"pattern": "needle_xyz", "file_path": build_folder}, _fake_render_context(build_folder))

    assert len(result) <= tools.MAX_INLINE_OUTPUT_CHARS + 1_000
    assert "bundle.js" in result
    assert "line truncated" in result


def _fake_memory_context(memory_folder: str) -> SimpleNamespace:
    return SimpleNamespace(
        memory_manager=SimpleNamespace(memory_folder=memory_folder),
        conformance_tests_running_context=SimpleNamespace(session_memory_notes=[]),
    )


def test_write_memory_scope_routing_and_tracking(project_dir):
    render_context = _fake_memory_context(project_dir)

    result = tools.write_memory({"file_name": "module-note.md", "content": "a module fact"}, render_context)
    assert "module scope" in result
    assert Path(project_dir, "agent_memory", "module-note.md").read_text(encoding="utf-8") == "a module fact"

    result = tools.write_memory(
        {"file_name": "build-note.md", "content": "a build-system fact", "scope": "global"}, render_context
    )
    assert "global scope" in result
    assert Path(project_dir, "global_memory", "build-note.md").read_text(encoding="utf-8") == "a build-system fact"

    tracked = render_context.conformance_tests_running_context.session_memory_notes
    assert [Path(p).name for p in tracked] == ["module-note.md", "build-note.md"]


def test_write_memory_rejects_invalid_scope(project_dir):
    render_context = _fake_memory_context(project_dir)
    result = tools.write_memory({"file_name": "x.md", "content": "y", "scope": "universe"}, render_context)
    assert result.startswith("Error: scope must be")


def test_review_action_memory_curation_helpers(project_dir):
    from render_machine.actions.review_conformance_fix_action import ReviewConformanceFixAction

    good = Path(project_dir, "good-note.md")
    bad = Path(project_dir, "bad-note.md")
    good.write_text("service X must be running before tests", encoding="utf-8")
    bad.write_text("just pkill all python processes in @BeforeAll", encoding="utf-8")
    ctx = SimpleNamespace(session_memory_notes=[str(good), str(bad)])

    notes = ReviewConformanceFixAction._read_session_memory_notes(ctx)
    assert set(notes) == {"good-note.md", "bad-note.md"}
    assert "pkill" in notes["bad-note.md"]

    ReviewConformanceFixAction._delete_rejected_memory_notes(ctx, ["bad-note.md"], "canonizes process-killing")
    assert not bad.exists()
    assert good.exists()
    assert ctx.session_memory_notes == [str(good)]

    # No-ops are safe.
    ReviewConformanceFixAction._delete_rejected_memory_notes(ctx, [], "")
    ReviewConformanceFixAction._delete_rejected_memory_notes(None, ["bad-note.md"], "")
    assert ctx.session_memory_notes == [str(good)]


def _find_server_repo() -> Path | None:
    """Locate the sibling server repository, if checked out."""
    env_override = os.environ.get("CODEPLAIN_SERVER_REPO")
    candidates = [Path(env_override)] if env_override else []
    parent = Path(__file__).resolve().parents[2]
    candidates += [parent / name for name in ("plain2code_rest_api", "server-agent", "server-main")]
    for candidate in candidates:
        if candidate and (candidate / "src" / "agent" / "tools.py").is_file():
            return candidate
    return None


def test_tool_names_match_server_definitions():
    """Every tool the server offers the model must be handled by this client, and
    every tool this client implements must be defined on the server.

    Skipped when the server repository is not checked out as a sibling (set
    CODEPLAIN_SERVER_REPO to point at it explicitly).
    """
    server_repo = _find_server_repo()
    if server_repo is None:
        pytest.skip("server repository not found next to this checkout")

    server_tools_source = (server_repo / "src" / "agent" / "tools.py").read_text(encoding="utf-8")
    server_tool_names = {
        line.split("def ", 1)[1].split("(", 1)[0]
        for line in server_tools_source.splitlines()
        if line.startswith("def ")
    }

    # Compatibility aliases the client keeps for older servers; not part of the
    # current server contract.
    client_only_aliases = {"think"}
    client_tool_names = (set(DEFAULT_TOOLS) | TERMINAL_TOOLS) - client_only_aliases

    assert server_tool_names == client_tool_names, (
        f"Tool contract drift between server definitions and client implementations.\n"
        f"Defined on server but not handled by client: {sorted(server_tool_names - client_tool_names)}\n"
        f"Handled by client but not defined on server: {sorted(client_tool_names - server_tool_names)}"
    )


def test_read_file_resolves_build_relative_path(project_dir, monkeypatch):
    """A path relative to the build folder (as listed in the codebase map) resolves
    into the build folder when it doesn't exist at the project root."""
    monkeypatch.chdir(project_dir)
    build_folder = os.path.join(project_dir, "plain_modules", "my_module")
    target = os.path.join(build_folder, "src", "main", "java", "App.java")
    os.makedirs(os.path.dirname(target))
    with open(target, "w") as f:
        f.write("public class App {}\n")

    result = tools.read_file({"file_path": "src/main/java/App.java"}, _fake_render_context(build_folder))

    assert "public class App" in result


def test_read_file_project_root_stays_authoritative(project_dir, monkeypatch):
    """A relative path that exists at the project root is never reinterpreted."""
    monkeypatch.chdir(project_dir)
    build_folder = os.path.join(project_dir, "build")
    os.makedirs(build_folder)
    with open(os.path.join(project_dir, "notes.txt"), "w") as f:
        f.write("root copy")
    with open(os.path.join(build_folder, "notes.txt"), "w") as f:
        f.write("build copy")

    result = tools.read_file({"file_path": "notes.txt"}, _fake_render_context(build_folder))

    assert "root copy" in result


def test_write_file_new_file_lands_in_build_tree(project_dir, monkeypatch):
    """Writing a new build-relative path whose parent exists under the build folder
    creates the file there — not in a fresh tree at the project root."""
    monkeypatch.chdir(project_dir)
    build_folder = os.path.join(project_dir, "build")
    os.makedirs(os.path.join(build_folder, "src", "main", "java"))

    result = tools.write_file(
        {"file_path": "src/main/java/New.java", "content": "public class New {}\n"},
        _fake_render_context(build_folder),
    )

    assert "Successfully wrote" in result
    assert os.path.exists(os.path.join(build_folder, "src", "main", "java", "New.java"))
    assert not os.path.exists(os.path.join(project_dir, "src"))
