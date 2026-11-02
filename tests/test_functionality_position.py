"""The TUI names the functionality being implemented and where it stands in its module.

The func-id says which functionality it is. It does not say how far the module has come, which the
number before the renderer took func-ids used to say.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import file_utils  # noqa: F401  # imported before plain_modules, which it imports back from
from plain2code_events import RenderContextSnapshot
from plain2code_utils import functionality_position
from render_machine.actions.render_functional_requirement import RenderFunctionalRequirement
from tui.state_handlers import FridReadyHandler

FRIDS = ["add-login-button", "add-logout-button", "remember-the-session"]


class TestFunctionalityPosition:
    def test_the_first_functionality(self):
        assert functionality_position(FRIDS, "add-login-button") == "1/3"

    def test_the_last_functionality(self):
        assert functionality_position(FRIDS, "remember-the-session") == "3/3"

    @pytest.mark.parametrize("frid", [None, "no-such-functionality"], ids=["none", "unknown"])
    def test_nothing_to_place(self, frid):
        assert functionality_position(FRIDS, frid) is None

    def test_a_module_with_one_functionality(self):
        assert functionality_position(["only-one"], "only-one") == "1/1"


def _snapshot(position):
    return RenderContextSnapshot(
        frid_context=SimpleNamespace(frid="add-login-button", functional_requirement_text="Add a login button."),
        conformance_tests_running_context=None,
        unit_tests_running_context=None,
        script_execution_history=None,
        module_name="",
        functionality_position=position,
    )


def _functionality_line(position):
    handler = FridReadyHandler(MagicMock(), unittests_script=None, conformance_tests_script=None)
    with (
        patch("tui.state_handlers.get_frid_progress") as get_progress,
        patch("tui.state_handlers.update_progress_item_status"),
    ):
        handler.handle([], _snapshot(position), [])
    return get_progress.return_value.update_functionality_text.call_args.args[0]


def test_the_tui_line_carries_the_position():
    assert _functionality_line("3/12") == "Functionality add-login-button (3/12): Add a login button."


def test_the_tui_line_without_a_position_reads_as_before():
    assert _functionality_line(None) == "Functionality add-login-button: Add a login button."


def _log_heading(frids, frid, attempts=1):
    """The heading the renderer logs when it starts a functionality."""
    render_context = SimpleNamespace(
        module_name="shop",
        frid_context=SimpleNamespace(
            frid=frid,
            functional_requirement_text="Add a login button.",
            functional_requirement_render_attempts=attempts,
        ),
        current_functionality_position=lambda: functionality_position(frids, frid),
    )
    return RenderFunctionalRequirement.functionality_heading(render_context)


def test_the_log_heading_carries_the_position():
    assert "Rendering functionality add-login-button (1/3):" in _log_heading(FRIDS, "add-login-button")


def test_the_log_heading_keeps_the_attempt_after_the_position():
    heading = _log_heading(FRIDS, "add-login-button", attempts=2)

    assert "Rendering functionality add-login-button (1/3), attempt number 2/" in heading


def test_the_log_heading_without_a_position_reads_as_before():
    assert "Rendering functionality add-login-button:" in _log_heading([], "add-login-button")
