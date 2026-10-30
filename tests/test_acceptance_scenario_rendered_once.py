"""An acceptance scenario is rendered once, whatever path enters the render action.

The action reads the scenario from `acceptance_tests_completed`, a counter the state machine owns.
A path that enters the action again without moving the counter asked for the same scenario a second
time, which cost a call and let the render write a second test for the one scenario.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import plain_spec
from render_machine.actions.render_conformance_tests import RenderConformanceTests
from render_machine.render_types import AcceptanceTestPhase, ConformanceTestsRunningContext

MODULE = "vault_functions"
FRID = "change-encryption-key"
SCENARIOS = [
    {"id": "the-old-key-no-longer-opens-the-vault", "text": "..."},
    {"id": "change-with-a-wrong-current-key", "text": "..."},
]


@pytest.fixture
def render_context(tmp_path):
    ctx = ConformanceTestsRunningContext(
        current_testing_module_name=MODULE,
        current_testing_frid=FRID,
        fix_attempts=0,
        conformance_tests_json={FRID: {"folder_name": str(tmp_path)}},
        conformance_tests_render_attempts=0,
        current_testing_frid_specifications={plain_spec.ACCEPTANCE_TESTS: SCENARIOS},
        should_prepare_testing_environment=False,
        frid_being_implemented=FRID,
    )
    ctx.acceptance_tests_completed = 1

    api = MagicMock()
    api.render_acceptance_tests.return_value = ({}, {"implemented": {}, "missing": [], "extra": []})

    conformance_tests = MagicMock()
    conformance_tests.fetch_existing_conformance_test_files.return_value = ({}, {})

    return SimpleNamespace(
        module_name=MODULE,
        build_folder=str(tmp_path),
        plain_source_tree={},
        required_modules=[],
        run_state=SimpleNamespace(),
        codeplain_api=api,
        conformance_tests=conformance_tests,
        conformance_tests_running_context=ctx,
        memory_manager=SimpleNamespace(memory_folder=str(tmp_path)),
        frid_context=SimpleNamespace(
            frid=FRID, linked_resources={}, specifications={plain_spec.ACCEPTANCE_TESTS: SCENARIOS}
        ),
        get_required_modules_functionalities=lambda: {},
    )


def _render(render_context):
    with (
        patch("render_machine.actions.render_conformance_tests.ImplementationCodeHelpers") as code_helpers,
        patch("render_machine.actions.render_conformance_tests.MemoryManager") as memory,
        patch("render_machine.actions.render_conformance_tests.file_utils"),
        patch("render_machine.actions.render_conformance_tests.console") as console,
    ):
        code_helpers.fetch_existing_files.return_value = ({}, {})
        memory.fetch_memory_files.return_value = ({}, {})
        outcome, _ = RenderConformanceTests()._render_acceptance_test(render_context)
    return outcome, [call.args[0] for call in console.warning.call_args_list]


def test_the_scenario_is_rendered_and_recorded(render_context):
    outcome, warnings = _render(render_context)

    assert outcome == RenderConformanceTests.SUCCESSFUL_OUTCOME
    assert render_context.codeplain_api.render_acceptance_tests.call_count == 1
    assert render_context.conformance_tests_running_context.rendered_acceptance_scenarios == [SCENARIOS[0]["id"]]
    assert warnings == []


def test_a_second_entry_without_the_counter_moving_renders_nothing(render_context):
    _render(render_context)

    outcome, warnings = _render(render_context)

    assert outcome == RenderConformanceTests.SUCCESSFUL_OUTCOME
    # The call that would have written a second test for the scenario is not made.
    assert render_context.codeplain_api.render_acceptance_tests.call_count == 1
    assert len(warnings) == 1
    assert SCENARIOS[0]["id"] in warnings[0]


def test_the_next_scenario_is_rendered_once_the_counter_moves(render_context):
    _render(render_context)
    render_context.conformance_tests_running_context.acceptance_tests_completed = 2

    outcome, warnings = _render(render_context)

    assert outcome == RenderConformanceTests.SUCCESSFUL_OUTCOME
    assert render_context.codeplain_api.render_acceptance_tests.call_count == 2
    assert [call.args[8] for call in render_context.codeplain_api.render_acceptance_tests.call_args_list] == [
        SCENARIOS[0]["id"],
        SCENARIOS[1]["id"],
    ]
    assert warnings == []


def test_a_functionality_without_acceptance_scenarios_renders_nothing(render_context):
    render_context.frid_context.specifications = {}

    outcome, _ = _render(render_context)

    assert outcome == RenderConformanceTests.SUCCESSFUL_OUTCOME
    assert render_context.codeplain_api.render_acceptance_tests.call_count == 0


class TestRegeneratedConformanceTests:
    """Regeneration deletes the tests folder of the functionality, so the acceptance tests in it are
    gone too. The functionality must then write all of its tests again, from the first scenario."""

    @staticmethod
    def make_render_context(tmp_path, acceptance_tests_completed, rendered):
        from render_machine.render_context import RenderContext

        render_context = RenderContext.__new__(RenderContext)
        render_context.module_name = MODULE
        folder = tmp_path / "tests_folder"
        folder.mkdir()

        ctx = ConformanceTestsRunningContext(
            current_testing_module_name=MODULE,
            current_testing_frid=FRID,
            fix_attempts=5,
            conformance_tests_json={FRID: {"folder_name": str(folder)}},
            conformance_tests_render_attempts=0,
            current_testing_frid_specifications={plain_spec.ACCEPTANCE_TESTS: SCENARIOS},
            should_prepare_testing_environment=False,
            frid_being_implemented=FRID,
        )
        ctx.acceptance_test_phase = AcceptanceTestPhase.IN_PROGRESS
        ctx.acceptance_tests_completed = acceptance_tests_completed
        ctx.rendered_acceptance_scenarios = list(rendered)
        ctx.regenerating_conformance_tests = True
        render_context.conformance_tests_running_context = ctx

        return render_context

    def test_the_acceptance_phase_starts_again(self, tmp_path):
        render_context = self.make_render_context(tmp_path, 1, [SCENARIOS[0]["id"]])

        with patch("render_machine.render_context.console"):
            render_context._handle_test_regeneration()

        ctx = render_context.conformance_tests_running_context
        assert ctx.acceptance_test_phase == AcceptanceTestPhase.NOT_STARTED
        assert ctx.acceptance_tests_completed == 0
        assert ctx.rendered_acceptance_scenarios == []

    def test_the_deleted_folder_is_no_longer_claimed(self, tmp_path):
        render_context = self.make_render_context(tmp_path, 1, [SCENARIOS[0]["id"]])

        with patch("render_machine.render_context.console"):
            render_context._handle_test_regeneration()

        ctx = render_context.conformance_tests_running_context
        assert not ctx.current_conformance_tests_exist()
