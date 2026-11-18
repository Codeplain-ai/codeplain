"""End-to-end tests for RESTART_FRID_PROCESSING state and repository cleanup."""

import gc
import os
import stat
import sys
import threading
from pathlib import Path
from typing import cast

import file_utils  # noqa: F401  # imported before plain_modules to avoid circular import
import git_utils
import render_machine.render_context as render_context_module
from event_bus import EventBus
from memory_management import MemoryManager
from plain2code_state import RunState
from plain_modules import PlainModule
from render_machine.code_renderer import CodeRenderer
from render_machine.render_context import RenderContext
from render_machine.states import States


def _make_script(directory: Path, name: str, ps1_body: str, sh_body: str) -> str:
    if sys.platform == "win32":
        script = directory / f"{name}.ps1"
        script.write_text(ps1_body, encoding="utf-8")
    else:
        script = directory / f"{name}.sh"
        script.write_text(sh_body, encoding="utf-8")
        os.chmod(script, os.stat(script).st_mode | stat.S_IEXEC)
    return str(script)


def _make_test_scripts(tmp_path: Path) -> tuple[str, str]:
    scripts = tmp_path / "scripts"
    scripts.mkdir(exist_ok=True)
    unit_script = _make_script(
        scripts,
        "unit",
        ps1_body=(
            "$build = $args[0]\n"
            '$content = Get-Content (Join-Path $build "mod.py") -Raw\n'
            'if ($content -match "fixed by conf attempt 1|fail unit attempt 1") { Write-Output "UNIT FAIL"; exit 1 }\n'
            "exit 0\n"
        ),
        sh_body=(
            "#!/bin/sh\n"
            'if grep -qE "fixed by conf attempt 1|fail unit attempt 1" "$1/mod.py"; then\n'
            '    echo "UNIT FAIL"\n'
            "    exit 1\n"
            "fi\n"
            "exit 0\n"
        ),
    )
    conf_script = _make_script(
        scripts,
        "conf",
        ps1_body=(
            "$build = $args[0]; $testdir = $args[1]\n"
            '$code = Get-Content (Join-Path $build "mod.py") -Raw\n'
            '$tfile = Get-Content (Join-Path $testdir "test_conformance.py") -Raw\n'
            'if (($code -match "refactored|attempt 1") -and ($tfile -match "original test")) {\n'
            '    Write-Output "CONF FAIL"\n'
            "    exit 1\n"
            "}\n"
            "exit 0\n"
        ),
        sh_body=(
            "#!/bin/sh\n"
            'if grep -qE "refactored|attempt 1" "$1/mod.py" && grep -q "original test" "$2/test_conformance.py"; then\n'
            '    echo "CONF FAIL"\n'
            "    exit 1\n"
            "fi\n"
            "exit 0\n"
        ),
    )
    return unit_script, conf_script


def _cleanup_dir(path: Path) -> None:
    gc.collect()
    try:
        file_utils.delete_folder(str(path))
    except PermissionError:
        pass


class _ScriptedRestartAPI:
    def __init__(self, target_frid: str, *, fail_in_impl_first_attempt: bool = False):
        self.target_frid = target_frid
        self.fail_in_impl_first_attempt = fail_in_impl_first_attempt
        self.target_render_calls = 0
        self.refactor_calls: list[tuple[str, int, list[str]]] = []
        self.distill_calls: list[tuple[str, list[dict]]] = []
        self.folder_idx = 0
        self.agent_sessions = 0
        self.agent_turn = 0

    def render_functional_requirement(
        self,
        frid,
        plain_source_tree,
        linked_resources,
        existing_files_content,
        memory_files_content,
        module_name,
        required_modules_functionalities,
        should_run_unit_tests,
        run_state,
    ):
        if frid != self.target_frid:
            return {"mod.py": f"# {frid}\n"}
        self.target_render_calls += 1
        if self.target_render_calls == 1:
            content = "# fail unit attempt 1\n" if self.fail_in_impl_first_attempt else f"# {frid} attempt 1\n"
            return {"mod.py": content, "attempt1_only.py": "# discarded\n"}
        return {"mod.py": f"# {frid} attempt 2\n"}

    def refactor_source_files_if_needed(self, frid, module_name, files_to_check, existing_files_content, run_state):
        if frid != self.target_frid:
            return {}
        self.refactor_calls.append((frid, self.target_render_calls, sorted(files_to_check)))
        if self.target_render_calls == 1:
            return {"mod.py": f"# refactored {len(self.refactor_calls)}\n"}
        return {}

    def generate_folder_name_from_functional_requirement(self, *_args, **_kwargs):
        self.folder_idx += 1
        return f"tests_{self.folder_idx}"

    def render_conformance_tests(self, frid, current_testing_frid, *_args, **_kwargs):
        return (
            {"test_conformance.py": f"# original test {current_testing_frid}\n"},
            {"implemented": {}, "missing": [], "extra": []},
        )

    def render_acceptance_tests(self, *_args, **_kwargs):
        return ({}, {"implemented": {}, "missing": [], "extra": []})

    def summarize_finished_conformance_tests(self, *, frid, **_kwargs):
        return f"summary {frid}"

    def distill_conformance_test_memory(self, *, frid, fix_journals, **_kwargs):
        self.distill_calls.append((frid, fix_journals))
        return {}

    def analyze_rendering(self, *_args, **_kwargs):
        return None

    def finish_functional_requirement(self, *_args, **_kwargs):
        return None

    def agent_start(self, task_type, task_params, frid, module_name, run_state):
        self.agent_sessions += 1
        sid = f"sess_{self.agent_sessions}"
        if task_type == "fix_conformance_tests":
            return {
                "session_id": sid,
                "status": "tool_calls",
                "calls": [
                    {
                        "id": f"call_{sid}_1",
                        "name": "write_file",
                        "args": {
                            "file_path": os.path.join(task_params["conformance_tests_folder"], "test_conformance.py"),
                            "content": "# CORRUPTED BY ATTEMPT 1 FIX\n",
                        },
                    },
                    {
                        "id": f"call_{sid}_2",
                        "name": "write_file",
                        "args": {"file_path": "mod.py", "content": "# fixed by conf attempt 1\n"},
                    },
                    {
                        "id": f"call_{sid}_3",
                        "name": "submit_fix",
                        "args": {
                            "root_cause": "bad code",
                            "failing_reason": "BUG",
                            "changes_made": "updated mod.py and conformance test",
                        },
                    },
                ],
            }
        if task_type == "review_conformance_tests_fix":
            return {
                "session_id": sid,
                "status": "tool_calls",
                "calls": [
                    {"id": f"call_{sid}_rev", "name": "submit_review", "args": {"verdict": "approve", "comments": "ok"}}
                ],
            }
        if task_type == "fix_unit_tests":
            return {
                "session_id": sid,
                "status": "tool_calls",
                "calls": [{"id": f"call_{sid}_ut", "name": "submit_fix", "args": {"changes_made": "tried unit fix"}}],
            }
        raise ValueError(f"Unexpected task_type: {task_type}")

    def agent_continue(self, session_id, tool_results, frid, module_name, run_state):
        self.agent_turn += 1
        return {
            "session_id": session_id,
            "status": "tool_calls",
            "calls": [
                {"id": f"call_{session_id}_{self.agent_turn}", "name": "submit_fix", "args": {"changes_made": "retry"}}
            ],
        }


def _setup_project(tmp_path: Path, frids: tuple[str, ...]) -> tuple[Path, Path]:
    specs = tmp_path / "specs"
    tests_specs = specs / "tests" / "mod"
    tests_specs.mkdir(parents=True)
    fr_sections = "\n\n".join(f"# {frid}\nImplement {frid}." for frid in frids)
    (specs / "mod.plain").write_text(
        f"***definitions***\n- :App: is simple.\n\n***implementation reqs***\n- Simple.\n\n***functional specs***\n\n{fr_sections}\n",
        encoding="utf-8",
    )
    for frid in frids:
        (tests_specs / f"{frid}.plain").write_text(
            f"---\nmodule: mod\nfunc_id: {frid}\n---\n\n***conformance tests***\n\n# sc-{frid}\nRuns {frid}.\n\n***acceptance tests***\n",
            encoding="utf-8",
        )
    build_root = tmp_path / "build"
    build_root.mkdir()
    return specs, build_root


def _run_renderer(
    tmp_path: Path, specs: Path, build_root: Path, api, *, copy_conformance_tests: bool = False
) -> RenderContext:
    unit_script, conf_script = _make_test_scripts(tmp_path)
    pm = PlainModule("mod.plain", str(build_root), [str(specs)], [str(specs / "tests")], conformance_tests_enabled=True)
    os.makedirs(pm.module_build_folder, exist_ok=True)
    ctx = RenderContext(
        codeplain_api=api,
        memory_manager=MemoryManager(api, pm.module_memory_folder),
        plain_module=pm,
        build_folder=pm.module_build_folder,
        build_dest=str(tmp_path / "dist"),
        conformance_tests_dest=str(tmp_path / "dist_tests"),
        unittests_script=unit_script,
        conformance_tests_script=conf_script,
        prepare_environment_script=cast(str, None),
        copy_build=False,
        copy_conformance_tests=copy_conformance_tests,
        render_range=None,
        render_conformance_tests=True,
        base_folder=str(specs),
        run_state=RunState(spec_filename="mod.plain"),
        event_bus=EventBus(),
        stop_event=threading.Event(),
        enter_pause_event=threading.Event(),
    )
    CodeRenderer(ctx).run()
    return ctx


def test_restart_from_conformance_unit_failure_reverts_tests_refactoring_and_journals(tmp_path, monkeypatch):
    monkeypatch.setattr(render_context_module, "MAX_UNITTEST_FIX_ATTEMPTS", 2)
    try:
        specs, build_root = _setup_project(tmp_path, ("fr-one", "fr-two"))
        api = _ScriptedRestartAPI(target_frid="fr-two")
        ctx = _run_renderer(tmp_path, specs, build_root, api, copy_conformance_tests=True)

        assert ctx.state == States.RENDER_COMPLETED.value
        assert api.target_render_calls == 2
        assert ctx.last_error_message is None

        # Attempt 1 created tests_2 and modified tests_1/test_conformance.py during regression.
        # Both must be reverted so only tests_1 (unmodified) and tests_3 remain on disk and in dist_tests.
        tests_dir = Path(ctx.plain_module.module_conformance_tests_folder)
        subfolders = sorted(p.name for p in tests_dir.iterdir() if p.is_dir() and not p.name.startswith("."))
        assert subfolders == ["tests_1", "tests_3"]
        assert (tests_dir / "tests_1" / "test_conformance.py").read_text(encoding="utf-8") == "# original test fr-one\n"
        dist_tests = Path(ctx.conformance_tests_dest)
        assert sorted(p.name for p in dist_tests.iterdir() if p.is_dir()) == ["tests_1", "tests_3"]

        # Attempt 2 must refactor cleanly from iteration 0 and must not see discarded attempt1_only.py.
        attempt2_refactors = [(frid, files) for frid, attempt, files in api.refactor_calls if attempt == 2]
        assert attempt2_refactors == [("fr-two", ["mod.py"])]

        # Attempt 1's discarded fix journals must not be distilled after Attempt 2 passes cleanly.
        assert api.distill_calls == []
        assert not git_utils.is_dirty(tests_dir)
    finally:
        _cleanup_dir(tmp_path / "build")


def test_restart_first_frid_from_conformance_unit_failure_reverts_tests_to_initial_commit(tmp_path, monkeypatch):
    monkeypatch.setattr(render_context_module, "MAX_UNITTEST_FIX_ATTEMPTS", 2)
    try:
        specs, build_root = _setup_project(tmp_path, ("fr-one",))
        api = _ScriptedRestartAPI(target_frid="fr-one")
        ctx = _run_renderer(tmp_path, specs, build_root, api)

        assert ctx.state == States.RENDER_COMPLETED.value
        assert api.target_render_calls == 2
        tests_dir = Path(ctx.plain_module.module_conformance_tests_folder)
        subfolders = sorted(p.name for p in tests_dir.iterdir() if p.is_dir() and not p.name.startswith("."))
        assert subfolders == ["tests_2"]
        assert (tests_dir / "tests_2" / "test_conformance.py").read_text(encoding="utf-8") == "# original test fr-one\n"
        assert [(frid, files) for frid, attempt, files in api.refactor_calls if attempt == 2] == [
            ("fr-one", ["mod.py"])
        ]
    finally:
        _cleanup_dir(tmp_path / "build")


def test_restart_from_implementation_unit_failure_clears_changed_files(tmp_path, monkeypatch):
    monkeypatch.setattr(render_context_module, "MAX_UNITTEST_FIX_ATTEMPTS", 2)
    try:
        specs, build_root = _setup_project(tmp_path, ("fr-one",))
        api = _ScriptedRestartAPI(target_frid="fr-one", fail_in_impl_first_attempt=True)
        ctx = _run_renderer(tmp_path, specs, build_root, api)

        assert ctx.state == States.RENDER_COMPLETED.value
        assert api.target_render_calls == 2
        assert [(frid, files) for frid, attempt, files in api.refactor_calls if attempt == 2] == [
            ("fr-one", ["mod.py"])
        ]
    finally:
        _cleanup_dir(tmp_path / "build")
