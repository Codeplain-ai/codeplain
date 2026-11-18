"""A functionality whose test plain file holds no scenario: it is rendered, without conformance tests.

A functionality with no test plain file at all is a different case and stops the run.
"""

import os

import pytest

import file_utils  # noqa: F401  # imported before plain_modules, which it imports back from
import plain2code
from plain_modules import PlainModule

MODULE = "required_with_acceptance_tests.plain"
EMPTY_TEST_PLAIN_FILE = """---
module: required_with_acceptance_tests
func_id: entry-point
---

***conformance tests***

***acceptance tests***
"""


@pytest.fixture
def project(tmp_path, get_test_data_path):
    """A copy of the fixture project, so a test can edit its test plain files."""
    import shutil

    source = get_test_data_path("data/acceptance_tests_warning")
    specs = tmp_path / "specs"
    shutil.copytree(source, specs)
    return specs


def _build(project, tmp_path, module=MODULE):
    return PlainModule(
        module,
        str(tmp_path / "build"),
        [str(project)],
        [str(project / "tests")],
        conformance_tests_enabled=True,
    )


def test_a_test_plain_file_with_a_scenario_is_not_reported(project, tmp_path):
    module = _build(project, tmp_path)

    assert module.functionalities_without_scenarios == []


def test_a_test_plain_file_without_scenarios_is_reported(project, tmp_path):
    (project / "tests" / "required_with_acceptance_tests" / "entry-point.plain").write_text(EMPTY_TEST_PLAIN_FILE)

    module = _build(project, tmp_path)

    assert module.functionalities_without_scenarios == [("required_with_acceptance_tests", "entry-point")]


def test_a_missing_test_plain_file_stops_the_run(project, tmp_path):
    os.remove(project / "tests" / "required_with_acceptance_tests" / "entry-point.plain")

    with pytest.raises(Exception, match="has no test plain file"):
        _build(project, tmp_path)


def test_the_warning_names_every_functionality_without_scenarios(project, tmp_path, monkeypatch):
    (project / "tests" / "required_with_acceptance_tests" / "entry-point.plain").write_text(EMPTY_TEST_PLAIN_FILE)
    module = _build(project, tmp_path)

    warnings = []
    monkeypatch.setattr(plain2code.console, "warning", lambda message: warnings.append(message))
    plain2code.warn_about_functionalities_without_scenarios(module)

    assert len(warnings) == 1
    assert "required_with_acceptance_tests:entry-point" in warnings[0]
    assert "without conformance tests" in warnings[0]


def test_nothing_is_warned_when_every_functionality_has_a_scenario(project, tmp_path, monkeypatch):
    module = _build(project, tmp_path)

    warnings = []
    monkeypatch.setattr(plain2code.console, "warning", lambda message: warnings.append(message))
    plain2code.warn_about_functionalities_without_scenarios(module)

    assert warnings == []


def _create_passing_script(directory, name="pass_script"):
    import stat
    import sys

    if sys.platform == "win32":
        script = directory / f"{name}.ps1"
        script.write_text("exit 0\n", encoding="utf-8")
    else:
        script = directory / f"{name}.sh"
        script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        os.chmod(script, os.stat(script).st_mode | stat.S_IEXEC)
    return str(script)


class _FakeRenderAPI:
    def __init__(self):
        self.rendered_frids = []
        self.rendered_conformance_frids = []
        self.rendered_acceptance_frids = []
        self.summarized_frids = []
        self._folder_index = 0

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
        self.rendered_frids.append((module_name, frid))
        return {f"{module_name}.py": f"# implemented {frid}\n"}

    def refactor_source_files_if_needed(self, *_args, **_kwargs):
        return {}

    def generate_folder_name_from_functional_requirement(self, *_args, **_kwargs):
        self._folder_index += 1
        return f"tests_{self._folder_index}"

    def render_conformance_tests(self, frid, current_testing_frid, *_args, **_kwargs):
        self.rendered_conformance_frids.append(current_testing_frid)
        return (
            {"test_conformance.py": f"# test {current_testing_frid}\n"},
            {"implemented": {}, "missing": [], "extra": []},
        )

    def render_acceptance_tests(self, frid, *_args, **_kwargs):
        self.rendered_acceptance_frids.append(frid)
        return ({"test_acceptance.py": f"# acceptance {frid}\n"}, {"implemented": {}, "missing": [], "extra": []})

    def summarize_finished_conformance_tests(self, *, frid, **_kwargs):
        self.summarized_frids.append(frid)
        return f"summary for {frid}"

    def distill_conformance_test_memory(self, *_args, **_kwargs):
        return {}

    def analyze_rendering(self, *_args, **_kwargs):
        return None

    def finish_functional_requirement(self, *_args, **_kwargs):
        return None


def _cleanup_build_folder(path):
    import gc

    gc.collect()
    try:
        file_utils.delete_folder(str(path))
    except PermissionError:
        pass


def _run_code_renderer(project, tmp_path, module_name, api, conformance_script=None):
    import threading
    from typing import cast

    from event_bus import EventBus
    from memory_management import MemoryManager
    from plain2code_state import RunState
    from render_machine.code_renderer import CodeRenderer
    from render_machine.render_context import RenderContext
    from render_machine.states import States

    build_root = tmp_path / "build"
    build_root.mkdir(exist_ok=True)
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir(exist_ok=True)

    unittests_script = _create_passing_script(scripts_dir, "run_unit")
    if conformance_script is None:
        conformance_script = _create_passing_script(scripts_dir, "run_conf")

    plain_module = PlainModule(
        module_name,
        str(build_root),
        [str(project)],
        [str(project / "tests")],
        conformance_tests_enabled=True,
    )
    os.makedirs(plain_module.module_build_folder, exist_ok=True)

    render_context = RenderContext(
        codeplain_api=api,
        memory_manager=MemoryManager(api, plain_module.module_memory_folder),
        plain_module=plain_module,
        build_folder=plain_module.module_build_folder,
        build_dest=str(tmp_path / "dist"),
        conformance_tests_dest=str(tmp_path / "dist_tests"),
        unittests_script=unittests_script,
        conformance_tests_script=conformance_script,
        prepare_environment_script=cast(str, None),
        copy_build=False,
        copy_conformance_tests=False,
        render_range=None,
        render_conformance_tests=True,
        base_folder=str(project),
        run_state=RunState(spec_filename=module_name),
        event_bus=EventBus(),
        stop_event=threading.Event(),
        enter_pause_event=threading.Event(),
    )
    CodeRenderer(render_context).run()
    assert render_context.state == States.RENDER_COMPLETED.value
    return render_context


def _write_scenario_test_file(tests_dir, module_dir, frid):
    test_file = tests_dir / module_dir / f"{frid}.plain"
    test_file.parent.mkdir(parents=True, exist_ok=True)
    test_file.write_text(
        f"""---
module: {module_dir}
func_id: {frid}
---

***conformance tests***

# scenario-for-{frid}
Running {frid} succeeds.

***acceptance tests***
""",
        encoding="utf-8",
    )


def _write_empty_test_file(tests_dir, module_dir, frid):
    test_file = tests_dir / module_dir / f"{frid}.plain"
    test_file.parent.mkdir(parents=True, exist_ok=True)
    test_file.write_text(
        f"""---
module: {module_dir}
func_id: {frid}
---

***conformance tests***

***acceptance tests***
""",
        encoding="utf-8",
    )


def test_single_functionality_without_scenarios_completes_rendering(project, tmp_path):
    try:
        (project / "tests" / "required_with_acceptance_tests" / "entry-point.plain").write_text(
            EMPTY_TEST_PLAIN_FILE, encoding="utf-8"
        )
        api = _FakeRenderAPI()
        ctx = _run_code_renderer(project, tmp_path, MODULE, api)

        assert api.rendered_frids == [("required_with_acceptance_tests", "entry-point")]
        assert api.rendered_conformance_frids == []
        assert api.rendered_acceptance_frids == []
        assert api.summarized_frids == []
        assert ctx.conformance_tests.get_conformance_tests_json("required_with_acceptance_tests") == {}
    finally:
        _cleanup_build_folder(tmp_path / "build")


def test_functionality_without_scenarios_after_one_with_scenarios_runs_regression_only(project, tmp_path):
    try:
        (project / MODULE).write_text(
            """***definitions***

- :App: is a console application.

***implementation reqs***

- The :App: should be simple.

***functional specs***

# fr-one
:App: handles the first command.

# fr-two
:App: handles the second command.
""",
            encoding="utf-8",
        )
        _write_scenario_test_file(project / "tests", "required_with_acceptance_tests", "fr-one")
        _write_empty_test_file(project / "tests", "required_with_acceptance_tests", "fr-two")
        os.remove(project / "tests" / "required_with_acceptance_tests" / "entry-point.plain")

        api = _FakeRenderAPI()
        ctx = _run_code_renderer(project, tmp_path, MODULE, api)

        assert api.rendered_frids == [
            ("required_with_acceptance_tests", "fr-one"),
            ("required_with_acceptance_tests", "fr-two"),
        ]
        assert api.rendered_conformance_frids == ["fr-one"]
        assert api.rendered_acceptance_frids == []
        assert api.summarized_frids == ["fr-one"]
        conformance_json = ctx.conformance_tests.get_conformance_tests_json("required_with_acceptance_tests")
        assert list(conformance_json.keys()) == ["fr-one"]
    finally:
        _cleanup_build_folder(tmp_path / "build")


def test_sandwiched_functionality_without_scenarios_is_skipped_during_later_regression(project, tmp_path):
    try:
        (project / MODULE).write_text(
            """***definitions***

- :App: is a console application.

***implementation reqs***

- The :App: should be simple.

***functional specs***

# fr-one
:App: handles the first command.

# fr-two
:App: handles the second command.

# fr-three
:App: handles the third command.
""",
            encoding="utf-8",
        )
        _write_scenario_test_file(project / "tests", "required_with_acceptance_tests", "fr-one")
        _write_empty_test_file(project / "tests", "required_with_acceptance_tests", "fr-two")
        _write_scenario_test_file(project / "tests", "required_with_acceptance_tests", "fr-three")
        os.remove(project / "tests" / "required_with_acceptance_tests" / "entry-point.plain")

        api = _FakeRenderAPI()
        ctx = _run_code_renderer(project, tmp_path, MODULE, api)

        assert api.rendered_conformance_frids == ["fr-one", "fr-three"]
        assert api.summarized_frids == ["fr-one", "fr-three"]
        conformance_json = ctx.conformance_tests.get_conformance_tests_json("required_with_acceptance_tests")
        assert list(conformance_json.keys()) == ["fr-one", "fr-three"]
    finally:
        _cleanup_build_folder(tmp_path / "build")


def test_required_module_without_scenarios_is_skipped_in_regression(project, tmp_path):
    try:
        (project / "tests" / "required_with_acceptance_tests" / "entry-point.plain").write_text(
            EMPTY_TEST_PLAIN_FILE, encoding="utf-8"
        )
        _write_empty_test_file(project / "tests", "main_requiring_acceptance_tests", "display-hello")

        api = _FakeRenderAPI()
        _run_code_renderer(project, tmp_path, MODULE, api)

        # Now add two functionalities to main: one with scenarios, one without, requiring the empty module.
        (project / "main_requiring_acceptance_tests.plain").write_text(
            """---
requires:
  - required_with_acceptance_tests
---

***definitions***

- :MainApp: is a console application.

***implementation reqs***

- The :MainApp: should print hello.

***functional specs***

# display-hello
:MainApp: displays "hello, world".

# display-bye
:MainApp: displays "goodbye".
""",
            encoding="utf-8",
        )
        _write_scenario_test_file(project / "tests", "main_requiring_acceptance_tests", "display-hello")
        _write_empty_test_file(project / "tests", "main_requiring_acceptance_tests", "display-bye")

        main_api = _FakeRenderAPI()
        main_ctx = _run_code_renderer(project, tmp_path, "main_requiring_acceptance_tests.plain", main_api)

        assert main_api.rendered_conformance_frids == ["display-hello"]
        assert main_api.summarized_frids == ["display-hello"]
        assert list(
            main_ctx.conformance_tests.get_conformance_tests_json("main_requiring_acceptance_tests").keys()
        ) == ["display-hello"]
    finally:
        _cleanup_build_folder(tmp_path / "build")
