"""Assembly of the context sent to the server's environment planner."""

import argparse
import os

from env_check import context as context_module
from env_check.context import MAX_RESOURCE_CHARS, TRUNCATION_NOTICE, build_environment_context, collect_host_info


class FakeModule:
    def __init__(self, name, source, resources=None, required=None):
        self.module_name = name
        self.plain_source = source
        self.resources_list = resources or []
        self.template_dirs = ["."]
        self.all_required_modules = required or []


def make_args(**overrides):
    args = argparse.Namespace(
        unittests_script=None,
        conformance_tests_script=None,
        prepare_environment_script=None,
    )
    for key, value in overrides.items():
        setattr(args, key, value)
    return args


def test_every_module_in_the_tree_is_included():
    base = FakeModule("base", {"sections": []})
    api = FakeModule("api", {"sections": []})
    top = FakeModule("top", {"sections": []}, required=[base, api])

    context = build_environment_context(top, make_args())

    assert [module["module_name"] for module in context["modules"]] == ["base", "api", "top"]


def test_configured_scripts_are_read(tmp_path):
    script = tmp_path / "run_unittests.sh"
    script.write_text("#!/bin/bash\npytest -q\n")

    context = build_environment_context(FakeModule("m", {}), make_args(unittests_script=str(script)))

    assert context["test_scripts"]["unittests_script"]["content"] == "#!/bin/bash\npytest -q\n"
    assert "conformance_tests_script" not in context["test_scripts"]


def test_unreadable_script_does_not_raise(tmp_path):
    context = build_environment_context(
        FakeModule("m", {}), make_args(prepare_environment_script=str(tmp_path / "missing.sh"))
    )

    assert context["test_scripts"]["prepare_environment_script"]["content"] == ""


def test_large_resources_are_truncated(monkeypatch):
    monkeypatch.setattr(
        context_module.file_utils,
        "load_linked_resources",
        lambda template_dirs, resources_list, module_name: {"docs/huge.md": "x" * (MAX_RESOURCE_CHARS + 500)},
    )

    context = build_environment_context(FakeModule("m", {}, resources=[{"target": "docs/huge.md"}]), make_args())
    content = context["linked_resources"]["docs/huge.md"]

    assert len(content) == MAX_RESOURCE_CHARS + len(TRUNCATION_NOTICE)
    assert content.endswith(TRUNCATION_NOTICE)


def test_resource_loading_failure_is_not_fatal(monkeypatch):
    def explode(template_dirs, resources_list, module_name):
        raise FileNotFoundError("resource missing")

    monkeypatch.setattr(context_module.file_utils, "load_linked_resources", explode)

    context = build_environment_context(FakeModule("m", {}, resources=[{"target": "gone.md"}]), make_args())

    assert context["linked_resources"] == {}


def test_host_info_describes_the_machine():
    host_info = collect_host_info()

    assert host_info["platform"] == os.sys.platform
    assert host_info["python_version"]


def test_no_credentials_are_collected(monkeypatch):
    monkeypatch.setenv("SOME_SECRET_API_KEY", "sk-do-not-send-me")

    context = build_environment_context(FakeModule("m", {}), make_args())

    assert "sk-do-not-send-me" not in str(context)
