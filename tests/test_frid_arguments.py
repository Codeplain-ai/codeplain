"""--render-range and --render-from take func-ids."""

import pytest

from plain2code_arguments import parse_arguments


def _parse(tmp_path, *extra_args):
    plain_file = tmp_path / "project.plain"
    plain_file.write_text("***functional specs***\n\n# add-task\nA :User: can add a :Task:.\n")
    return parse_arguments([str(plain_file), *extra_args])


def test_render_from_takes_a_func_id(tmp_path):
    args = _parse(tmp_path, "--render-from", "add-task")

    assert args.render_from == "add-task"


def test_render_range_takes_two_func_ids(tmp_path):
    args = _parse(tmp_path, "--render-range", "add-task,list-tasks")

    assert args.render_range == "add-task,list-tasks"


def test_render_range_takes_one_func_id(tmp_path):
    args = _parse(tmp_path, "--render-range", "add-task")

    assert args.render_range == "add-task"


@pytest.mark.parametrize("value", ["Add-Task", "add task", "add_task", "add.task", ""])
def test_a_value_that_is_not_a_func_id_is_rejected(tmp_path, value):
    with pytest.raises(SystemExit):
        _parse(tmp_path, "--render-from", value)


def test_a_range_of_more_than_two_func_ids_is_rejected(tmp_path):
    with pytest.raises(SystemExit):
        _parse(tmp_path, "--render-range", "add-task,list-tasks,rename-task")
