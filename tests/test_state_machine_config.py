"""Tests for the render state machine's wiring.

The state machine is configured by three loosely coupled maps - states, transitions, and
state-to-action - plus a flat outcome-to-trigger map. A typo in any of them fails at render time
rather than at import time, so these tests check that they agree with each other.
"""

from unittest.mock import MagicMock

import pytest
from transitions.extensions.diagrams import HierarchicalGraphMachine

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
    """Conformance testing is a root-level phase, not a child of IMPLEMENTING_FRID."""

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

    def test_the_phase_is_entered_after_the_last_functionality(self, config, render_context):
        transitions = config.get_transitions(render_context)
        source = f"{States.IMPLEMENTING_FRID.value}_{States.FRID_FULLY_IMPLEMENTED.value}"

        module_phase_transitions = [
            transition
            for transition in transitions
            if transition["source"] == source and transition["dest"] == States.PROCESSING_MODULE_CONFORMANCE_TESTS.value
        ]

        assert len(module_phase_transitions) == 1
        transition = module_phase_transitions[0]
        assert render_context.should_run_conformance_tests in transition["conditions"]
        assert render_context.has_next_frid in transition["unless"]

    def test_implementing_a_functionality_never_runs_conformance_tests(self, config, render_context):
        """Conformance testing is reached only from the root level, once every frid is implemented."""
        transitions = config.get_transitions(render_context)

        frid_scoped_conformance = [
            transition
            for transition in transitions
            if transition["dest"].startswith(f"{States.IMPLEMENTING_FRID.value}_")
            and "onformanceTests" in transition["dest"]
        ]

        assert frid_scoped_conformance == []

    def test_refactoring_leads_straight_to_the_functionality_being_done(self, config, render_context):
        transitions = config.get_transitions(render_context)
        source = (
            f"{States.IMPLEMENTING_FRID.value}_{States.REFACTORING_CODE.value}_{States.READY_FOR_REFACTORING.value}"
        )

        destinations = {
            transition["dest"]
            for transition in transitions
            if transition["source"] == source and transition["trigger"] == triggers_module.PROCEED_FRID_PROCESSING
        }

        assert destinations == {f"{States.IMPLEMENTING_FRID.value}_{States.FRID_FULLY_IMPLEMENTED.value}"}


class _FakeRenderContext:
    """A render context that only knows how to answer the machine's questions.

    It stands in for RenderContext so the topology can be walked without a build folder, a git repo
    or an API. The callbacks the configuration wires up are declared explicitly rather than caught by
    a __getattr__, because the machine binds its own trigger methods onto the model and a catch-all
    would shadow them.
    """

    def __init__(self, number_of_frids: int, conformance_tests_enabled: bool = True):
        self.frids_left = number_of_frids
        self.conformance_tests_enabled = conformance_tests_enabled

    # ---- predicates the transitions are guarded on ----
    def has_next_frid(self):
        return self.frids_left > 0

    def should_run_unit_tests(self):
        return True

    def should_run_conformance_tests(self):
        return self.conformance_tests_enabled

    # ---- state callbacks ----
    def start_implementing_frid(self):
        # Consumes a functionality, the way the real callback walks on to the next frid.
        self.frids_left -= 1

    def finish_implementing_frid(self):
        pass

    def start_unittests_processing(self):
        pass

    def finish_unittests_processing(self):
        pass

    def start_fixing_unit_tests(self, _on_limit_exceeded):
        pass

    def _on_unit_test_limit_exceeded_in_implementation(self):
        pass

    def _on_unit_test_limit_exceeded_in_refactoring(self):
        pass

    def _on_unit_test_limit_exceeded_in_module_conformance_tests(self):
        pass

    def start_module_conformance_tests_processing(self):
        pass

    def finish_module_conformance_tests_processing(self):
        pass

    def start_render_completed(self):
        pass

    def start_render_failed(self):
        pass


def _walk(machine, context, trigger):
    machine.dispatch(trigger)
    return context.state


class TestRenderFlowOrder:
    """The order the phases actually run in: every functionality first, conformance at the very end."""

    def _machine_for(self, config, context):
        return HierarchicalGraphMachine(
            model=context,
            states=config.get_states(context),
            transitions=config.get_transitions(context),
            initial=States.RENDER_INITIALISED.value,
        )

    def _implement_one_functionality(self, machine, context):
        """Drive one functionality through implementation, unit tests and refactoring."""
        visited = [_walk(machine, context, triggers_module.RENDER_FUNCTIONAL_REQUIREMENT)]
        visited.append(_walk(machine, context, triggers_module.MARK_UNIT_TESTS_PASSED))
        visited.append(_walk(machine, context, triggers_module.PROCEED_FRID_PROCESSING))
        visited.append(_walk(machine, context, triggers_module.REFACTOR_CODE))
        visited.append(_walk(machine, context, triggers_module.MARK_UNIT_TESTS_PASSED))
        visited.append(_walk(machine, context, triggers_module.PROCEED_FRID_PROCESSING))
        visited.append(_walk(machine, context, triggers_module.PROCEED_FRID_PROCESSING))
        return visited

    def test_conformance_testing_only_starts_after_the_last_functionality(self, config):
        context = _FakeRenderContext(number_of_frids=3)
        machine = self._machine_for(config, context)

        _walk(machine, context, triggers_module.START_RENDER)

        visited_per_functionality = []
        for _ in range(3):
            visited_per_functionality.append(self._implement_one_functionality(machine, context))
            # Leaving a functionality either starts the next one or enters conformance testing.
            visited_per_functionality[-1].append(_walk(machine, context, triggers_module.PROCEED_FRID_PROCESSING))

        # No conformance state is visited while functionalities are still being implemented.
        while_implementing = [state for states in visited_per_functionality[:-1] for state in states]
        assert not any("onformance" in state for state in while_implementing)

        # The last functionality hands over to the module conformance phase.
        assert context.state.startswith(States.PROCESSING_MODULE_CONFORMANCE_TESTS.value)

    def test_the_conformance_phase_plans_then_implements_then_runs_then_finishes(self, config):
        context = _FakeRenderContext(number_of_frids=1)
        machine = self._machine_for(config, context)

        _walk(machine, context, triggers_module.START_RENDER)
        self._implement_one_functionality(machine, context)
        _walk(machine, context, triggers_module.PROCEED_FRID_PROCESSING)

        phase = States.PROCESSING_MODULE_CONFORMANCE_TESTS.value
        assert context.state == f"{phase}_{States.MODULE_CONFORMANCE_TESTING_INITIALISED.value}"

        # Planning, then a batch of the plan, then another step (an acceptance test) into the suite.
        assert (
            _walk(machine, context, triggers_module.MARK_MODULE_CONFORMANCE_TESTS_PLANNED)
            == f"{phase}_{States.MODULE_CONFORMANCE_TESTS_PLANNED.value}"
        )
        assert (
            _walk(machine, context, triggers_module.MARK_MODULE_CONFORMANCE_TESTS_PLANNED)
            == f"{phase}_{States.MODULE_CONFORMANCE_TESTS_PLANNED.value}"
        )

        # Suite complete: prepare the environment, then run.
        assert (
            _walk(machine, context, triggers_module.MARK_MODULE_CONFORMANCE_TESTS_READY)
            == f"{phase}_{States.MODULE_CONFORMANCE_TESTS_GENERATED.value}"
        )
        assert (
            _walk(machine, context, triggers_module.MARK_MODULE_TESTING_ENVIRONMENT_PREPARED)
            == f"{phase}_{States.MODULE_CONFORMANCE_TESTS_ENV_PREPARED.value}"
        )

        # A suite passed and another one is pending: back through environment preparation.
        assert (
            _walk(machine, context, triggers_module.MOVE_TO_NEXT_MODULE_CONFORMANCE_SUITE)
            == f"{phase}_{States.MODULE_CONFORMANCE_TESTS_GENERATED.value}"
        )
        _walk(machine, context, triggers_module.MARK_MODULE_TESTING_ENVIRONMENT_PREPARED)

        # Every suite passed: summarize, commit, analyze, finish, done.
        assert _walk(machine, context, triggers_module.MARK_ALL_MODULE_CONFORMANCE_TESTS_PASSED) == (
            f"{phase}_{States.POSTPROCESSING_MODULE_CONFORMANCE_TESTS.value}_"
            f"{States.MODULE_CONFORMANCE_TESTS_READY_FOR_SUMMARY.value}"
        )
        assert _walk(machine, context, triggers_module.MARK_NEXT_MODULE_CONFORMANCE_TESTS_POSTPROCESSING_STEP) == (
            f"{phase}_{States.POSTPROCESSING_MODULE_CONFORMANCE_TESTS.value}_"
            f"{States.MODULE_CONFORMANCE_TESTS_READY_FOR_COMMIT.value}"
        )
        assert _walk(machine, context, triggers_module.MARK_NEXT_MODULE_CONFORMANCE_TESTS_POSTPROCESSING_STEP) == (
            f"{phase}_{States.POSTPROCESSING_MODULE_CONFORMANCE_TESTS.value}_"
            f"{States.MODULE_CONFORMANCE_TESTS_READY_FOR_AMBIGUITY_ANALYSIS.value}"
        )
        assert (
            _walk(machine, context, triggers_module.PROCEED_MODULE_CONFORMANCE_TESTING)
            == f"{phase}_{States.MODULE_FULLY_IMPLEMENTED.value}"
        )
        assert (
            _walk(machine, context, triggers_module.PROCEED_MODULE_CONFORMANCE_TESTING) == States.RENDER_COMPLETED.value
        )

    def test_a_failing_suite_is_fixed_and_re_run(self, config):
        context = _FakeRenderContext(number_of_frids=1)
        machine = self._machine_for(config, context)
        phase = States.PROCESSING_MODULE_CONFORMANCE_TESTS.value

        _walk(machine, context, triggers_module.START_RENDER)
        self._implement_one_functionality(machine, context)
        _walk(machine, context, triggers_module.PROCEED_FRID_PROCESSING)
        _walk(machine, context, triggers_module.MARK_MODULE_CONFORMANCE_TESTS_PLANNED)
        _walk(machine, context, triggers_module.MARK_MODULE_CONFORMANCE_TESTS_READY)
        _walk(machine, context, triggers_module.MARK_MODULE_TESTING_ENVIRONMENT_PREPARED)

        assert (
            _walk(machine, context, triggers_module.MARK_MODULE_CONFORMANCE_TESTS_FAILED)
            == f"{phase}_{States.MODULE_CONFORMANCE_TESTS_FAILED.value}"
        )

        # A fix confined to the test code re-runs the same suite.
        assert (
            _walk(machine, context, triggers_module.MARK_MODULE_CONFORMANCE_TESTS_READY)
            == f"{phase}_{States.MODULE_CONFORMANCE_TESTS_ENV_PREPARED.value}"
        )

        # A fix that touched the implementation code goes through the unit tests first.
        _walk(machine, context, triggers_module.MARK_MODULE_CONFORMANCE_TESTS_FAILED)
        assert (
            _walk(machine, context, triggers_module.MARK_UNIT_TESTS_READY)
            == f"{phase}_{States.PROCESSING_UNIT_TESTS.value}_{States.UNIT_TESTS_READY.value}"
        )
        assert (
            _walk(machine, context, triggers_module.MARK_UNIT_TESTS_PASSED)
            == f"{phase}_{States.MODULE_CONFORMANCE_TESTS_GENERATED.value}"
        )

    def test_conformance_testing_is_skipped_without_a_conformance_script(self, config):
        context = _FakeRenderContext(number_of_frids=1, conformance_tests_enabled=False)
        machine = self._machine_for(config, context)

        _walk(machine, context, triggers_module.START_RENDER)
        self._implement_one_functionality(machine, context)

        assert _walk(machine, context, triggers_module.PROCEED_FRID_PROCESSING) == States.RENDER_COMPLETED.value
