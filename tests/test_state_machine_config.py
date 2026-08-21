"""Tests for the render state machine's wiring.

The state machine is configured by three loosely coupled maps - states, transitions, and
state-to-action - plus a flat outcome-to-trigger map. A typo in any of them fails at render time
rather than at import time, so these tests check that they agree with each other.
"""

from unittest.mock import MagicMock

import pytest
from transitions.extensions.diagrams import HierarchicalGraphMachine

from plain2code_arguments import CONFORMANCE_SCOPE_FUNCTIONALITY, CONFORMANCE_SCOPE_MODULE
from render_machine import triggers as triggers_module
from render_machine.state_machine_config import StateMachineConfig
from render_machine.states import States


@pytest.fixture
def config():
    return StateMachineConfig()


@pytest.fixture
def render_context():
    """A stand-in for RenderContext.

    The configuration only ever uses the context as a source of callables (state callbacks and
    transition conditions), so a mock is enough to build the machine.
    """
    return MagicMock()


class _Model:
    """A bare model for the machine to bind onto.

    A MagicMock cannot be used here: it answers to every attribute, including get_graph, which the
    diagrams machine refuses to overwrite.
    """


@pytest.fixture
def machine(config, render_context):
    return HierarchicalGraphMachine(
        model=_Model(),
        states=config.get_states(render_context),
        transitions=config.get_transitions(render_context),
        initial=States.RENDER_INITIALISED.value,
    )


def _all_state_names(machine):
    """Every state the machine has, addressed the way the renderer addresses them.

    machine.states holds only the root states; nested states are reachable under their parents and
    are addressed by the underscore-joined path that render_context.state reports.
    """
    return set(machine.get_nested_state_names())


def _leaf_state_names(machine):
    """The states the renderer can actually rest in, and therefore needs an action for."""
    all_names = _all_state_names(machine)

    return {name for name in all_names if not any(other.startswith(f"{name}_") for other in all_names)}


class TestActionMap:
    def test_every_state_with_an_action_exists_in_the_machine(self, config, machine):
        """A state name typo in the action map would only surface as a KeyError mid-render."""
        missing = [state for state in config.get_action_map() if state not in _all_state_names(machine)]

        assert missing == []

    def test_every_state_the_machine_can_rest_in_has_an_action(self, config, machine):
        """The renderer looks up an action for whatever state it is in, so every leaf state needs one."""
        action_map = config.get_action_map()

        missing = sorted(state for state in _leaf_state_names(machine) if state not in action_map)

        assert missing == []


class TestOutcomeToTriggerMap:
    def test_outcomes_are_unique_across_actions(self, config):
        """The outcome map is flat, so two actions sharing an outcome string would collide."""
        outcomes = list(config.get_action_result_triggers_map().keys())

        assert len(outcomes) == len(set(outcomes))

    def test_every_outcome_maps_to_a_declared_trigger(self, config):
        declared_triggers = {
            value for name, value in vars(triggers_module).items() if name.isupper() and isinstance(value, str)
        }

        unknown = [
            trigger for trigger in config.get_action_result_triggers_map().values() if trigger not in declared_triggers
        ]

        assert unknown == []

    def test_every_mapped_trigger_is_used_by_a_transition(self, config, render_context):
        transition_triggers = {transition["trigger"] for transition in config.get_transitions(render_context)}

        # These are dispatched imperatively from RenderContext rather than through the outcome map.
        imperative_triggers = {
            triggers_module.RESTART_FRID_PROCESSING,
            triggers_module.START_NEW_REFACTORING_ITERATION,
            triggers_module.MARK_ALL_CONFORMANCE_TESTS_PASSED,
        }
        # CreateDist runs while the machine already rests in RENDER_COMPLETED, and the renderer breaks
        # out of its loop before dispatching, so this trigger never needs a transition.
        never_dispatched_triggers = {triggers_module.FINISH_RENDER}

        unused = [
            trigger
            for trigger in config.get_action_result_triggers_map().values()
            if trigger not in transition_triggers
            and trigger not in imperative_triggers
            and trigger not in never_dispatched_triggers
        ]

        assert unused == []


class TestModuleConformanceTopology:
    """Module-scoped conformance testing is a root-level phase, not a child of IMPLEMENTING_FRID."""

    def test_module_conformance_phase_is_a_root_state(self, config, render_context):
        root_state_names = {
            state if isinstance(state, str) else state["name"] for state in config.get_states(render_context)
        }

        assert States.PROCESSING_MODULE_CONFORMANCE_TESTS.value in root_state_names

    def test_module_conformance_phase_has_its_own_unit_test_states(self, config, machine):
        """An implementation fix made during the phase has to be reconciled with the unit tests."""
        unit_tests_state = (
            f"{States.PROCESSING_MODULE_CONFORMANCE_TESTS.value}_"
            f"{States.PROCESSING_UNIT_TESTS.value}_{States.UNIT_TESTS_READY.value}"
        )

        assert unit_tests_state in _all_state_names(machine)

    def test_module_conformance_phase_leads_to_render_completed(self, config, render_context):
        transitions = config.get_transitions(render_context)
        source = f"{States.PROCESSING_MODULE_CONFORMANCE_TESTS.value}_{States.MODULE_FULLY_IMPLEMENTED.value}"

        destinations = {transition["dest"] for transition in transitions if transition["source"] == source}

        assert destinations == {States.RENDER_COMPLETED.value}

    def test_last_functionality_enters_the_module_phase_only_in_module_scope(self, config, render_context):
        transitions = config.get_transitions(render_context)
        source = f"{States.IMPLEMENTING_FRID.value}_{States.FRID_FULLY_IMPLEMENTED.value}"

        module_phase_transitions = [
            transition
            for transition in transitions
            if transition["source"] == source and transition["dest"] == States.PROCESSING_MODULE_CONFORMANCE_TESTS.value
        ]

        assert len(module_phase_transitions) == 1
        transition = module_phase_transitions[0]
        assert render_context.should_run_module_conformance_tests in transition["conditions"]
        assert render_context.has_next_frid in transition["unless"]

    def test_per_functionality_conformance_is_gated_on_the_functionality_scope(self, config, render_context):
        """Under module scope, implementing a functionality must not run conformance tests for it."""
        transitions = config.get_transitions(render_context)
        source = (
            f"{States.IMPLEMENTING_FRID.value}_{States.REFACTORING_CODE.value}_" f"{States.READY_FOR_REFACTORING.value}"
        )

        conformance_transitions = [
            transition
            for transition in transitions
            if transition["source"] == source
            and transition["dest"] == f"{States.IMPLEMENTING_FRID.value}_{States.PROCESSING_CONFORMANCE_TESTS.value}"
        ]

        assert len(conformance_transitions) == 1
        assert conformance_transitions[0]["conditions"] == render_context.should_run_frid_conformance_tests


class TestConformanceScopeValues:
    def test_the_two_scopes_are_distinct(self):
        assert CONFORMANCE_SCOPE_FUNCTIONALITY != CONFORMANCE_SCOPE_MODULE
