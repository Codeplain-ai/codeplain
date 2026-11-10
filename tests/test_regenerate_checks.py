"""The checks that stop a regeneration that cannot run, and the messages they give.

Both checks run before anything is rendered, and both name the regeneration. The checks for
--render-from would otherwise answer first and talk about starting a render the user did not ask for.
"""

import os
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

import plain2code
from git_utils import FUNCTIONAL_REQUIREMENT_FINISHED_COMMIT_MESSAGE, add_all_files_and_commit, init_git_repo
from plain2code_exceptions import InvalidFridArgument, MissingPreviousFunctionalitiesError
from plain_modules import PlainModule


@pytest.fixture
def solo_module(get_test_data_path):
    with tempfile.TemporaryDirectory() as build_folder:
        yield PlainModule("pr_solo.plain", build_folder, [get_test_data_path("data/partial_rendering")])


def _commit_finished_frid(module: PlainModule, frid: str) -> None:
    (Path(module.module_build_folder) / f"frid_{frid}.txt").write_text(f"frid {frid}\n")
    add_all_files_and_commit(
        module.module_build_folder,
        FUNCTIONAL_REQUIREMENT_FINISHED_COMMIT_MESSAGE.format(frid),
        module_name=module.module_name,
        frid=frid,
    )


def _rendered_up_to(module: PlainModule, *frids: str) -> None:
    os.makedirs(module.module_build_folder)
    init_git_repo(module.module_build_folder, module_name=module.module_name)
    for frid in frids:
        _commit_finished_frid(module, frid)


def test_a_rendered_functionality_can_be_regenerated(solo_module):
    _rendered_up_to(solo_module, "solo-one", "solo-two")

    solo_module.ensure_frid_can_be_regenerated("solo-two")


def test_a_functionality_after_the_rendered_ones_cannot_be_regenerated(solo_module):
    """The previous functionality is not rendered either, but the message is about the regeneration."""
    _rendered_up_to(solo_module, "solo-one")

    with pytest.raises(MissingPreviousFunctionalitiesError) as error:
        solo_module.ensure_frid_can_be_regenerated("solo-three")

    assert str(error.value).startswith("Cannot regenerate functionality solo-three because it has not been")
    assert "--render-from" not in str(error.value)


def test_a_module_that_was_never_rendered_cannot_be_regenerated(solo_module):
    with pytest.raises(MissingPreviousFunctionalitiesError) as error:
        solo_module.ensure_frid_can_be_regenerated("solo-one")

    assert str(error.value).startswith("Cannot regenerate functionality solo-one because it has not been")


def test_an_unknown_func_id_names_the_regeneration(solo_module):
    args = SimpleNamespace(render_range=None, render_from=None, regenerate="no-such-id")

    with pytest.raises(InvalidFridArgument) as error:
        plain2code.render(solo_module, None, args, None, None)

    assert str(error.value) == (
        "Cannot regenerate functionality no-such-id because module pr_solo has no such functionality. "
        "Its functionalities are: solo-one, solo-two, solo-three."
    )
