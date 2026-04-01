"""Tests for ModuleRenderer collect_render_plan and prompt_user_if_needed."""
import argparse
from unittest.mock import MagicMock, call, patch

import pytest

from module_renderer import ModuleRenderInfo, ModuleRenderer


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_args(**overrides):
    defaults = {
        "build_folder": "plain_modules",
        "conformance_tests_folder": "conformance_tests",
        "build_dest": "dist",
        "conformance_tests_dest": "dist_conformance_tests",
        "render_conformance_tests": False,
        "force_render": False,
        "render_machine_graph": False,
        "copy_build": False,
        "copy_conformance_tests": False,
        "unittests_script": None,
        "conformance_tests_script": None,
        "prepare_environment_script": None,
        "verbose": False,
        "base_folder": None,
        "test_script_timeout": None,
        "yes": False,
        "headless": False,
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def make_renderer(filename="module_c.plain", render_range=None, **args_overrides):
    return ModuleRenderer(
        codeplainAPI=MagicMock(),
        filename=filename,
        render_range=render_range,
        template_dirs=[],
        args=make_args(**args_overrides),
        run_state=MagicMock(),
        event_bus=MagicMock(),
    )


def make_info(
    module_name="module_c",
    change_reasons=None,
    all_frids=None,
    implemented_frids=None,
    no_prior_render=False,
):
    return ModuleRenderInfo(
        module_name=module_name,
        change_reasons=change_reasons or [],
        all_frids=all_frids or ["1", "2", "3", "4", "5", "6"],
        implemented_frids=implemented_frids or [],
        no_prior_render=no_prior_render,
    )


def make_plain_module_mock(spec_changed=False, required_code_changed=False, has_repo=True):
    m = MagicMock()
    m.get_repo.return_value = object() if has_repo else None
    m.has_plain_spec_changed.return_value = spec_changed
    m.has_required_modules_code_changed.return_value = required_code_changed
    return m


# ---------------------------------------------------------------------------
# collect_render_plan — dry-run traversal tests
# ---------------------------------------------------------------------------

class TestCollectRenderPlan:

    def _patch_all(self, parser_returns, module_mock, frids, has_commit_fn):
        """Return a context-manager-compatible patch stack for single-module tests."""
        return (
            patch("plain_file.plain_file_parser", side_effect=parser_returns),
            patch("plain_spec.collect_linked_resources"),
            patch("plain_modules.PlainModule", return_value=module_mock),
            patch("plain_spec.get_frids", return_value=frids),
            patch("git_utils.has_commit_for_frid", side_effect=has_commit_fn),
        )

    def test_no_changes_root_module_always_in_plan(self):
        """Root module is always in the plan (force_render=True), even with no spec changes.
        It appears with empty change_reasons reflecting that no changes were detected."""
        renderer = make_renderer()
        module_mock = make_plain_module_mock(spec_changed=False, required_code_changed=False)

        with patch("plain_file.plain_file_parser", return_value=("module_c", {}, [])), \
             patch("plain_spec.collect_linked_resources"), \
             patch("plain_modules.PlainModule", return_value=module_mock), \
             patch("plain_spec.get_frids", return_value=["1", "2", "3"]), \
             patch("git_utils.has_commit_for_frid", return_value=True):

            renderer.collect_render_plan()

        assert len(renderer._render_plan) == 1
        assert renderer._render_plan[0].change_reasons == []
        assert renderer._render_plan[0].module_name == "module_c"

    def test_no_changes_required_module_not_in_plan(self):
        """Required modules with no changes and force_render=False are skipped from the plan."""
        renderer = make_renderer()

        module_b_mock = make_plain_module_mock(spec_changed=False, required_code_changed=False)
        module_b_mock.name = "module_b"
        module_c_mock = make_plain_module_mock(spec_changed=False, required_code_changed=False)
        module_c_mock.name = "module_c"

        def parser_side_effect(filename, _):
            if "module_b" in filename:
                return ("module_b", {}, [])
            return ("module_c", {}, ["module_b"])

        def plain_module_side_effect(name, _):
            return module_b_mock if name == "module_b" else module_c_mock

        with patch("plain_file.plain_file_parser", side_effect=parser_side_effect), \
             patch("plain_spec.collect_linked_resources"), \
             patch("plain_modules.PlainModule", side_effect=plain_module_side_effect), \
             patch("plain_spec.get_frids", return_value=["1", "2"]), \
             patch("git_utils.has_commit_for_frid", return_value=True):

            renderer.collect_render_plan()

        # module_b (required, force_render=False, no changes) should be skipped
        # module_c (root, force_render=True) always appears
        names = [i.module_name for i in renderer._render_plan]
        assert "module_b" not in names
        assert "module_c" in names

    def test_no_prior_render_added_to_plan(self):
        renderer = make_renderer()
        module_mock = make_plain_module_mock(has_repo=False)

        with patch("plain_file.plain_file_parser", return_value=("module_c", {}, [])), \
             patch("plain_spec.collect_linked_resources"), \
             patch("plain_modules.PlainModule", return_value=module_mock), \
             patch("plain_spec.get_frids", return_value=["1", "2", "3"]):

            renderer.collect_render_plan()

        assert len(renderer._render_plan) == 1
        info = renderer._render_plan[0]
        assert info.module_name == "module_c"
        assert info.no_prior_render is True
        assert info.implemented_frids == []
        assert info.change_reasons == []

    def test_spec_changed_partially_rendered(self):
        renderer = make_renderer()
        module_mock = make_plain_module_mock(spec_changed=True)

        with patch("plain_file.plain_file_parser", return_value=("module_c", {}, [])), \
             patch("plain_spec.collect_linked_resources"), \
             patch("plain_modules.PlainModule", return_value=module_mock), \
             patch("plain_spec.get_frids", return_value=["1", "2", "3", "4", "5", "6"]), \
             patch("git_utils.has_commit_for_frid", side_effect=lambda p, f, m: f in {"1", "2", "3"}):

            renderer.collect_render_plan()

        assert len(renderer._render_plan) == 1
        info = renderer._render_plan[0]
        assert info.change_reasons == ["spec changed"]
        assert info.all_frids == ["1", "2", "3", "4", "5", "6"]
        assert info.implemented_frids == ["1", "2", "3"]
        assert info.no_prior_render is False

    def test_spec_changed_fully_rendered(self):
        renderer = make_renderer()
        module_mock = make_plain_module_mock(spec_changed=True)

        with patch("plain_file.plain_file_parser", return_value=("module_c", {}, [])), \
             patch("plain_spec.collect_linked_resources"), \
             patch("plain_modules.PlainModule", return_value=module_mock), \
             patch("plain_spec.get_frids", return_value=["1", "2", "3"]), \
             patch("git_utils.has_commit_for_frid", return_value=True):

            renderer.collect_render_plan()

        info = renderer._render_plan[0]
        assert info.implemented_frids == ["1", "2", "3"]
        assert info.all_frids == ["1", "2", "3"]

    def test_required_module_spec_changed_propagates(self):
        """Required module spec change causes both modules to appear in plan."""
        renderer = make_renderer()

        module_b_mock = make_plain_module_mock(spec_changed=True)
        module_b_mock.name = "module_b"
        module_c_mock = make_plain_module_mock(spec_changed=False, required_code_changed=True)
        module_c_mock.name = "module_c"

        def parser_side_effect(filename, _):
            if "module_b" in filename:
                return ("module_b", {}, [])
            return ("module_c", {}, ["module_b"])

        def plain_module_side_effect(name, build_folder):
            if name == "module_b":
                return module_b_mock
            return module_c_mock

        with patch("plain_file.plain_file_parser", side_effect=parser_side_effect), \
             patch("plain_spec.collect_linked_resources"), \
             patch("plain_modules.PlainModule", side_effect=plain_module_side_effect), \
             patch("plain_spec.get_frids", return_value=["1", "2", "3"]), \
             patch("git_utils.has_commit_for_frid", return_value=True):

            renderer.collect_render_plan()

        assert len(renderer._render_plan) == 2
        assert renderer._render_plan[0].module_name == "module_b"
        assert renderer._render_plan[1].module_name == "module_c"
        assert "spec changed" in renderer._render_plan[0].change_reasons
        assert any("required module" in r for r in renderer._render_plan[1].change_reasons)

    def test_chain_a_b_c_spec_change_in_a(self):
        """A spec change in A propagates through B to C — all three in plan."""
        renderer = make_renderer()

        mocks = {
            "module_a": make_plain_module_mock(spec_changed=True),
            "module_b": make_plain_module_mock(spec_changed=False, required_code_changed=True),
            "module_c": make_plain_module_mock(spec_changed=False, required_code_changed=True),
        }
        for name, m in mocks.items():
            m.name = name

        def parser_side_effect(filename, _):
            if "module_a" in filename:
                return ("module_a", {}, [])
            if "module_b" in filename:
                return ("module_b", {}, ["module_a"])
            return ("module_c", {}, ["module_b"])

        def plain_module_side_effect(name, _):
            return mocks[name]

        with patch("plain_file.plain_file_parser", side_effect=parser_side_effect), \
             patch("plain_spec.collect_linked_resources"), \
             patch("plain_modules.PlainModule", side_effect=plain_module_side_effect), \
             patch("plain_spec.get_frids", return_value=["1", "2", "3"]), \
             patch("git_utils.has_commit_for_frid", return_value=True):

            renderer.collect_render_plan()

        names = [i.module_name for i in renderer._render_plan]
        assert names == ["module_a", "module_b", "module_c"]


# ---------------------------------------------------------------------------
# prompt_user_if_needed — situation tests
# (These set _render_plan directly to test prompt logic in isolation.)
# ---------------------------------------------------------------------------

class TestPromptUserIfNeeded:

    def test_empty_plan_no_prompt(self, capsys):
        renderer = make_renderer()
        renderer._render_plan = []

        result = renderer.prompt_user_if_needed()

        assert result is False
        assert capsys.readouterr().out == ""

    def test_situation_11_no_prior_render_no_changes_no_prompt(self, capsys):
        renderer = make_renderer()
        renderer._render_plan = [make_info(no_prior_render=True, change_reasons=[])]

        result = renderer.prompt_user_if_needed()

        assert result is False
        assert capsys.readouterr().out == ""

    # --- Situation 1: required module changed, current partially rendered ---

    def test_situation_1_shows_correct_message(self, capsys):
        renderer = make_renderer()
        renderer._render_plan = [
            make_info("module_b", change_reasons=["spec changed"], all_frids=["1", "2"], implemented_frids=["1", "2"]),
            make_info("module_c", change_reasons=["required module 'module_b' changed"],
                      all_frids=["1", "2", "3", "4", "5", "6"], implemented_frids=["1", "2", "3"]),
        ]

        with patch("builtins.input", return_value="d"):
            renderer.prompt_user_if_needed()

        out = capsys.readouterr().out
        assert "Changes in specs in module_b have been identified." in out
        assert "This would require re-rendering of the following modules:" in out
        assert "module_b" in out
        assert "module_c" in out
        assert "functionalities 1, 2, 3 were already implemented" in out
        assert "Continue from functionality 4" in out
        assert "Re-render module_c from scratch" in out

    def test_situation_1_choice_a_rerenders_all(self, capsys):
        renderer = make_renderer()
        renderer._render_plan = [
            make_info("module_b", change_reasons=["spec changed"], all_frids=["1", "2"], implemented_frids=["1", "2"]),
            make_info("module_c", change_reasons=["required module 'module_b' changed"],
                      all_frids=["1", "2", "3", "4", "5", "6"], implemented_frids=["1", "2", "3"]),
        ]

        with patch("builtins.input", return_value="a"):
            result = renderer.prompt_user_if_needed()

        assert result is False
        assert renderer._skip_required_rerender is False
        assert renderer.render_range is None

    def test_situation_1_choice_b_continues_from_frid(self, capsys):
        renderer = make_renderer()
        renderer._render_plan = [
            make_info("module_b", change_reasons=["spec changed"], all_frids=["1", "2"], implemented_frids=["1", "2"]),
            make_info("module_c", change_reasons=["required module 'module_b' changed"],
                      all_frids=["1", "2", "3", "4", "5", "6"], implemented_frids=["1", "2", "3"]),
        ]

        with patch("builtins.input", return_value="b"):
            result = renderer.prompt_user_if_needed()

        assert result is False
        assert renderer._skip_required_rerender is True
        assert renderer.render_range == ["4", "5", "6"]

    def test_situation_1_choice_c_rerenders_current_from_scratch(self, capsys):
        renderer = make_renderer()
        renderer._render_plan = [
            make_info("module_b", change_reasons=["spec changed"], all_frids=["1", "2"], implemented_frids=["1", "2"]),
            make_info("module_c", change_reasons=["required module 'module_b' changed"],
                      all_frids=["1", "2", "3", "4", "5", "6"], implemented_frids=["1", "2", "3"]),
        ]

        with patch("builtins.input", return_value="c"):
            result = renderer.prompt_user_if_needed()

        assert result is False
        assert renderer._skip_required_rerender is True
        assert renderer.render_range is None

    def test_situation_1_choice_d_cancels(self, capsys):
        renderer = make_renderer()
        renderer._render_plan = [
            make_info("module_b", change_reasons=["spec changed"], all_frids=["1", "2"], implemented_frids=["1", "2"]),
            make_info("module_c", change_reasons=["required module 'module_b' changed"],
                      all_frids=["1", "2", "3", "4", "5", "6"], implemented_frids=["1", "2", "3"]),
        ]

        with patch("builtins.input", return_value="d"):
            result = renderer.prompt_user_if_needed()

        assert result is True

    # --- Situation 2: required module changed, current fully rendered ---

    def test_situation_2_shows_rerender_current_from_scratch_option(self, capsys):
        renderer = make_renderer()
        renderer._render_plan = [
            make_info("module_b", change_reasons=["spec changed"], all_frids=["1", "2"], implemented_frids=["1", "2"]),
            make_info("module_c", change_reasons=["required module 'module_b' changed"],
                      all_frids=["1", "2", "3"], implemented_frids=["1", "2", "3"]),
        ]

        with patch("builtins.input", return_value="c"):
            renderer.prompt_user_if_needed()

        out = capsys.readouterr().out
        assert "all functionalities were already implemented" in out
        assert "Re-render module_c from scratch" in out

    def test_situation_2_choice_b_sets_skip_rerender(self, capsys):
        renderer = make_renderer()
        renderer._render_plan = [
            make_info("module_b", change_reasons=["spec changed"], all_frids=["1", "2"], implemented_frids=["1", "2"]),
            make_info("module_c", change_reasons=["required module 'module_b' changed"],
                      all_frids=["1", "2", "3"], implemented_frids=["1", "2", "3"]),
        ]

        with patch("builtins.input", return_value="b"):
            result = renderer.prompt_user_if_needed()

        assert result is False
        assert renderer._skip_required_rerender is True
        assert renderer.render_range is None

    # --- Situation 3: spec changed in current module, partially rendered ---

    def test_situation_3_shows_continue_from_option(self, capsys):
        renderer = make_renderer()
        renderer._render_plan = [
            make_info("module_c", change_reasons=["spec changed"],
                      all_frids=["1", "2", "3", "4", "5", "6"], implemented_frids=["1", "2", "3"]),
        ]

        with patch("builtins.input", return_value="c"):
            renderer.prompt_user_if_needed()

        out = capsys.readouterr().out
        assert "Changes in specs in module_c have been identified." in out
        assert "Continue from functionality 4" in out
        assert "Re-render all" in out

    def test_situation_3_choice_b_sets_render_range(self, capsys):
        renderer = make_renderer()
        renderer._render_plan = [
            make_info("module_c", change_reasons=["spec changed"],
                      all_frids=["1", "2", "3", "4", "5", "6"], implemented_frids=["1", "2", "3"]),
        ]

        with patch("builtins.input", return_value="b"):
            renderer.prompt_user_if_needed()

        assert renderer.render_range == ["4", "5", "6"]
        assert renderer._skip_required_rerender is True

    # --- Situation 4: spec changed in current module, fully rendered ---

    def test_situation_4_only_rerender_or_cancel(self, capsys):
        renderer = make_renderer()
        renderer._render_plan = [
            make_info("module_c", change_reasons=["spec changed"],
                      all_frids=["1", "2", "3"], implemented_frids=["1", "2", "3"]),
        ]

        with patch("builtins.input", return_value="b"):
            result = renderer.prompt_user_if_needed()

        out = capsys.readouterr().out
        assert "all functionalities were already implemented" in out
        assert "Continue from" not in out
        assert result is True  # choice b = cancel in situation 4

    def test_situation_4_choice_a_rerenders(self, capsys):
        renderer = make_renderer()
        renderer._render_plan = [
            make_info("module_c", change_reasons=["spec changed"],
                      all_frids=["1", "2", "3"], implemented_frids=["1", "2", "3"]),
        ]

        with patch("builtins.input", return_value="a"):
            result = renderer.prompt_user_if_needed()

        assert result is False
        assert renderer._skip_required_rerender is False

    # --- Situation 7: chain change (A→B→C), current partially rendered ---

    def test_situation_7_lists_all_three_modules(self, capsys):
        renderer = make_renderer()
        renderer._render_plan = [
            make_info("module_a", change_reasons=["spec changed"], all_frids=["1", "2"], implemented_frids=["1", "2"]),
            make_info("module_b", change_reasons=["required module 'module_a' changed"], all_frids=["1", "2"], implemented_frids=["1", "2"]),
            make_info("module_c", change_reasons=["required module 'module_b' changed"],
                      all_frids=["1", "2", "3", "4", "5", "6"], implemented_frids=["1", "2", "3"]),
        ]

        with patch("builtins.input", return_value="c"):
            renderer.prompt_user_if_needed()

        out = capsys.readouterr().out
        assert "Changes in specs in module_a have been identified." in out
        assert "module_a" in out
        assert "module_b" in out
        assert "module_c" in out
        assert "Re-render all (module_a, module_b, module_c)" in out

    # --- Situation 8: chain change (A→B→C), current fully rendered ---

    def test_situation_8_rerender_current_from_scratch_option(self, capsys):
        renderer = make_renderer()
        renderer._render_plan = [
            make_info("module_a", change_reasons=["spec changed"], all_frids=["1"], implemented_frids=["1"]),
            make_info("module_b", change_reasons=["required module 'module_a' changed"], all_frids=["1"], implemented_frids=["1"]),
            make_info("module_c", change_reasons=["required module 'module_b' changed"],
                      all_frids=["1", "2", "3"], implemented_frids=["1", "2", "3"]),
        ]

        with patch("builtins.input", return_value="b"):
            result = renderer.prompt_user_if_needed()

        assert result is False
        assert renderer._skip_required_rerender is True
        assert renderer.render_range is None

    # --- Situation 9: required module changed, current not yet rendered ---

    def test_situation_9_not_rendered_shows_render_current_option(self, capsys):
        renderer = make_renderer()
        renderer._render_plan = [
            make_info("module_b", change_reasons=["spec changed"], all_frids=["1"], implemented_frids=["1"]),
            make_info("module_c", change_reasons=["required module 'module_b' changed"],
                      no_prior_render=True, all_frids=["1", "2", "3"], implemented_frids=[]),
        ]

        with patch("builtins.input", return_value="c"):
            result = renderer.prompt_user_if_needed()

        out = capsys.readouterr().out
        assert "not yet rendered" in out
        assert "Continue from" not in out
        assert f"Render module_c" in out
        assert result is True  # c = cancel in situation 9

    def test_situation_9_choice_a_rerenders_all(self, capsys):
        renderer = make_renderer()
        renderer._render_plan = [
            make_info("module_b", change_reasons=["spec changed"], all_frids=["1"], implemented_frids=["1"]),
            make_info("module_c", change_reasons=["required module 'module_b' changed"],
                      no_prior_render=True, all_frids=["1", "2", "3"], implemented_frids=[]),
        ]

        with patch("builtins.input", return_value="a"):
            result = renderer.prompt_user_if_needed()

        assert result is False
        assert renderer._skip_required_rerender is False

    def test_situation_9_choice_b_renders_current_only(self, capsys):
        renderer = make_renderer()
        renderer._render_plan = [
            make_info("module_b", change_reasons=["spec changed"], all_frids=["1"], implemented_frids=["1"]),
            make_info("module_c", change_reasons=["required module 'module_b' changed"],
                      no_prior_render=True, all_frids=["1", "2", "3"], implemented_frids=[]),
        ]

        with patch("builtins.input", return_value="b"):
            result = renderer.prompt_user_if_needed()

        assert result is False
        assert renderer._skip_required_rerender is True
        assert renderer.render_range is None

    # --- Situation 12: no spec changes, current partially rendered ---

    def test_situation_12_no_opening_line(self, capsys):
        renderer = make_renderer()
        renderer._render_plan = [
            make_info("module_c", change_reasons=[],
                      all_frids=["1", "2", "3", "4", "5", "6"], implemented_frids=["1", "2", "3"]),
        ]

        with patch("builtins.input", return_value="c"):
            renderer.prompt_user_if_needed()

        out = capsys.readouterr().out
        assert "Changes in specs" not in out
        assert "module_c has been partially rendered" in out
        assert "functionalities 1, 2, 3 were already implemented" in out
        assert "Continue from functionality 4" in out

    def test_situation_12_choice_b_sets_render_range(self, capsys):
        renderer = make_renderer()
        renderer._render_plan = [
            make_info("module_c", change_reasons=[],
                      all_frids=["1", "2", "3", "4", "5", "6"], implemented_frids=["1", "2", "3"]),
        ]

        with patch("builtins.input", return_value="b"):
            renderer.prompt_user_if_needed()

        assert renderer.render_range == ["4", "5", "6"]

    # --- Situation 13: no spec changes, current fully rendered ---

    def test_situation_13_no_opening_line(self, capsys):
        renderer = make_renderer()
        renderer._render_plan = [
            make_info("module_c", change_reasons=[],
                      all_frids=["1", "2", "3"], implemented_frids=["1", "2", "3"]),
        ]

        with patch("builtins.input", return_value="b"):
            renderer.prompt_user_if_needed()

        out = capsys.readouterr().out
        assert "Changes in specs" not in out
        assert "All functionalities in module_c were already implemented." in out
        assert "Re-render module_c from scratch" in out
        assert "Continue from" not in out

    # --- CLI render_range set: hides "Continue from" ---

    def test_cli_render_range_hides_continue_from(self, capsys):
        renderer = make_renderer(render_range=["3", "4", "5", "6"])
        renderer._render_plan = [
            make_info("module_b", change_reasons=["spec changed"], all_frids=["1", "2"], implemented_frids=["1", "2"]),
            make_info("module_c", change_reasons=["required module 'module_b' changed"],
                      all_frids=["1", "2", "3", "4", "5", "6"], implemented_frids=["1", "2", "3"]),
        ]

        with patch("builtins.input", return_value="b"):
            renderer.prompt_user_if_needed()

        out = capsys.readouterr().out
        assert "Continue from functionality" not in out
        assert f"Re-render {renderer._render_plan[-1].module_name} from scratch" in out

    # --- Invalid input re-prompts ---

    def test_invalid_input_reprompts(self, capsys):
        renderer = make_renderer()
        renderer._render_plan = [
            make_info("module_c", change_reasons=["spec changed"],
                      all_frids=["1", "2", "3"], implemented_frids=["1", "2", "3"]),
        ]

        with patch("builtins.input", side_effect=["z", "x", "a"]):
            result = renderer.prompt_user_if_needed()

        assert result is False
        out = capsys.readouterr().out
        assert out.count("Please enter one of") == 2

    # --- EOFError / KeyboardInterrupt cancel ---

    def test_eof_cancels(self):
        renderer = make_renderer()
        renderer._render_plan = [
            make_info("module_c", change_reasons=["spec changed"],
                      all_frids=["1", "2", "3"], implemented_frids=["1"]),
        ]

        with patch("builtins.input", side_effect=EOFError):
            result = renderer.prompt_user_if_needed()

        assert result is True

    def test_keyboard_interrupt_cancels(self):
        renderer = make_renderer()
        renderer._render_plan = [
            make_info("module_c", change_reasons=["spec changed"],
                      all_frids=["1", "2", "3"], implemented_frids=["1"]),
        ]

        with patch("builtins.input", side_effect=KeyboardInterrupt):
            result = renderer.prompt_user_if_needed()

        assert result is True


# ---------------------------------------------------------------------------
# _skip_required_rerender — integration with render_range updates
# ---------------------------------------------------------------------------

class TestUserDecisionState:

    def test_rerender_all_does_not_set_skip_flag(self, capsys):
        renderer = make_renderer()
        renderer._render_plan = [
            make_info("module_b", change_reasons=["spec changed"], all_frids=["1"], implemented_frids=["1"]),
            make_info("module_c", change_reasons=["required module 'module_b' changed"],
                      all_frids=["1", "2", "3"], implemented_frids=["1", "2", "3"]),
        ]

        with patch("builtins.input", return_value="a"):
            renderer.prompt_user_if_needed()

        assert renderer._skip_required_rerender is False

    def test_continue_from_computes_range_from_first_unimplemented(self, capsys):
        renderer = make_renderer()
        renderer._render_plan = [
            make_info("module_b", change_reasons=["spec changed"], all_frids=["1"], implemented_frids=["1"]),
            make_info("module_c", change_reasons=["required module 'module_b' changed"],
                      all_frids=["1", "2", "3", "4", "5"], implemented_frids=["1", "2"]),
        ]

        with patch("builtins.input", return_value="b"):
            renderer.prompt_user_if_needed()

        assert renderer.render_range == ["3", "4", "5"]
        assert renderer._skip_required_rerender is True

    def test_rerender_current_clears_render_range(self, capsys):
        renderer = make_renderer(render_range=["3", "4"])
        renderer._render_plan = [
            make_info("module_b", change_reasons=["spec changed"], all_frids=["1"], implemented_frids=["1"]),
            make_info("module_c", change_reasons=["required module 'module_b' changed"],
                      all_frids=["1", "2", "3", "4"], implemented_frids=["1", "2", "3", "4"]),
        ]

        with patch("builtins.input", return_value="b"):
            renderer.prompt_user_if_needed()

        assert renderer.render_range is None
        assert renderer._skip_required_rerender is True

    def test_cancel_does_not_modify_state(self, capsys):
        renderer = make_renderer()
        renderer._render_plan = [
            make_info("module_c", change_reasons=["spec changed"],
                      all_frids=["1", "2", "3"], implemented_frids=["1"]),
        ]
        original_render_range = renderer.render_range

        with patch("builtins.input", return_value="c"):
            result = renderer.prompt_user_if_needed()

        assert result is True
        assert renderer.render_range == original_render_range
        assert renderer._skip_required_rerender is False
