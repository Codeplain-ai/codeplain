"""The TUI names the functionality being implemented and where it stands in its module.

The func-id says which functionality it is. It does not say how far the module has come, which the
number before the renderer took func-ids used to say.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import file_utils  # noqa: F401  # imported before plain_modules, which it imports back from
import plain_spec
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


def test_testing_functionality_position_when_context_is_none():
    from render_machine.render_context import RenderContext

    ctx = SimpleNamespace(
        conformance_tests_running_context=None,
        module_name="app",
    )
    ctx.testing_functionality_position = lambda: RenderContext.testing_functionality_position(ctx)  # type: ignore[arg-type]
    ctx.testing_functionality_label = lambda: RenderContext.testing_functionality_label(ctx)  # type: ignore[arg-type]
    assert ctx.testing_functionality_position() is None
    assert ctx.testing_functionality_label() is None


def test_testing_functionality_position_for_transitive_required_module():
    from render_machine.render_context import RenderContext

    # Setup module hierarchy: app -> db -> core
    core_module = SimpleNamespace(
        module_name="core",
        plain_source={plain_spec.FUNCTIONAL_REQUIREMENTS: [{"id": "init-core"}, {"id": "cleanup-core"}]},
    )
    db_module = SimpleNamespace(
        module_name="db",
        plain_source={plain_spec.FUNCTIONAL_REQUIREMENTS: [{"id": "connect-db"}]},
    )
    app_module = SimpleNamespace(
        module_name="app",
        plain_source={plain_spec.FUNCTIONAL_REQUIREMENTS: [{"id": "start-app"}]},
        required_modules=[db_module],
        all_required_modules=[core_module, db_module],
    )

    ctx = SimpleNamespace(
        plain_module=app_module,
        required_modules=app_module.required_modules,
        plain_source_tree=app_module.plain_source,
        module_name="app",
        conformance_tests_running_context=SimpleNamespace(
            current_testing_module_name="core",
            current_testing_frid="cleanup-core",
        ),
    )
    ctx.testing_functionality_position = lambda: RenderContext.testing_functionality_position(ctx)  # type: ignore[arg-type]
    ctx.testing_functionality_label = lambda: RenderContext.testing_functionality_label(ctx)  # type: ignore[arg-type]

    # Transitive required module position and label resolution
    assert ctx.testing_functionality_position() == "2/2"
    assert ctx.testing_functionality_label() == "core:cleanup-core (2/2)"

    # Direct required module
    ctx.conformance_tests_running_context.current_testing_module_name = "db"
    ctx.conformance_tests_running_context.current_testing_frid = "connect-db"
    assert ctx.testing_functionality_position() == "1/1"
    assert ctx.testing_functionality_label() == "db:connect-db (1/1)"

    # Current module
    ctx.conformance_tests_running_context.current_testing_module_name = "app"
    ctx.conformance_tests_running_context.current_testing_frid = "start-app"
    assert ctx.testing_functionality_position() == "1/1"
    assert ctx.testing_functionality_label() == "app:start-app (1/1)"
