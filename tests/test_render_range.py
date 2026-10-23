from types import SimpleNamespace

import pytest

import plain_spec

FUNC_IDS = ["add-task", "list-tasks", "rename-task", "delete-task"]


def _four_functionalities():
    return {
        plain_spec.FUNCTIONAL_REQUIREMENTS: [
            {"id": func_id, "markdown": f"A :User: can {func_id.replace('-', ' ')}."} for func_id in FUNC_IDS
        ]
    }


def test_get_render_range_parses_start_and_end():
    assert plain_spec.get_render_range("list-tasks,rename-task", _four_functionalities()) == [
        "list-tasks",
        "rename-task",
    ]


def test_get_render_range_single_value_renders_one_frid():
    assert plain_spec.get_render_range("list-tasks", _four_functionalities()) == ["list-tasks"]


def test_get_render_range_from_runs_to_last_frid():
    assert plain_spec.get_render_range_from("rename-task", _four_functionalities()) == ["rename-task", "delete-task"]


def test_compute_render_range_prefers_render_range():
    args = SimpleNamespace(render_range="add-task,list-tasks", render_from="rename-task")
    assert plain_spec.compute_render_range(args, _four_functionalities()) == ["add-task", "list-tasks"]


def test_compute_render_range_falls_back_to_render_from():
    args = SimpleNamespace(render_range=None, render_from="rename-task")
    assert plain_spec.compute_render_range(args, _four_functionalities()) == ["rename-task", "delete-task"]


def test_compute_render_range_none_when_no_arguments():
    args = SimpleNamespace(render_range=None, render_from=None)
    assert plain_spec.compute_render_range(args, _four_functionalities()) is None


def test_compute_render_range_invalid_frid_raises():
    args = SimpleNamespace(render_range="no-such-task", render_from=None)
    with pytest.raises(plain_spec.InvalidFridArgument):
        plain_spec.compute_render_range(args, _four_functionalities())
