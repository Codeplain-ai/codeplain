from types import SimpleNamespace

import pytest

import plain_spec


def _four_functionalities():
    return {plain_spec.FUNCTIONAL_REQUIREMENTS: [{"markdown": f"- Functionality {i}."} for i in range(1, 5)]}


def test_get_render_range_parses_start_and_end():
    assert plain_spec.get_render_range("2,3", _four_functionalities()) == ["2", "3"]


def test_get_render_range_single_value_renders_one_frid():
    assert plain_spec.get_render_range("2", _four_functionalities()) == ["2"]


def test_get_render_range_from_runs_to_last_frid():
    assert plain_spec.get_render_range_from("3", _four_functionalities()) == ["3", "4"]


def test_compute_render_range_prefers_render_range():
    args = SimpleNamespace(render_range="1,2", render_from="3")
    assert plain_spec.compute_render_range(args, _four_functionalities()) == ["1", "2"]


def test_compute_render_range_falls_back_to_render_from():
    args = SimpleNamespace(render_range=None, render_from="3")
    assert plain_spec.compute_render_range(args, _four_functionalities()) == ["3", "4"]


def test_compute_render_range_none_when_no_arguments():
    args = SimpleNamespace(render_range=None, render_from=None)
    assert plain_spec.compute_render_range(args, _four_functionalities()) is None


def test_compute_render_range_invalid_frid_raises():
    args = SimpleNamespace(render_range="9", render_from=None)
    with pytest.raises(plain_spec.InvalidFridArgument):
        plain_spec.compute_render_range(args, _four_functionalities())
