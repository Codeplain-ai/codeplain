import time
from copy import deepcopy

from transitions.extensions.diagrams import HierarchicalGraphMachine

from plain2code_events import (
    RenderModuleCompleted,
    RenderModuleFailed,
    RenderModuleStarted,
    RenderPaused,
    RenderStateUpdated,
)
from plain2code_trace import trace
from render_machine.render_context import RenderContext
from render_machine.state_machine_config import StateMachineConfig, States

PAUSE_POLL_INTERVAL_SECONDS = 1


class CodeRenderer:
    """Main code renderer class that orchestrates the code generation workflow using a hierarchical state machine."""

    def __init__(self, render_context: RenderContext):
        self.render_context = render_context
        self.state_machine_config = StateMachineConfig()

        # Initialize the state machine
        states = self.state_machine_config.get_states(self.render_context)
        transitions = self.state_machine_config.get_transitions(self.render_context)

        self.machine = HierarchicalGraphMachine(
            model=self.render_context,
            states=states,
            transitions=transitions,
            initial=States.RENDER_INITIALISED.value,
        )
        self.render_context.set_machine(self.machine)

        # Get action mappings
        self.action_map = self.state_machine_config.get_action_map(use_agent=render_context.use_agent)
        self.action_result_triggers_map = self.state_machine_config.get_action_result_triggers_map()

    def run(self):
        """Execute the main rendering workflow."""
        self.render_context.event_bus.publish(RenderModuleStarted(module_name=self.render_context.module_name))
        self.render_context.run_state.current_module = self.render_context.module_name
        previous_action_payload = None
        previous_state = None

        while True:
            if self.render_context.enter_pause_event.is_set():
                self.render_context.event_bus.publish(RenderPaused())

                # don't take sleep time into account for render time
                self.render_context.run_state.add_to_render_time()
                while self.render_context.enter_pause_event.is_set():
                    time.sleep(PAUSE_POLL_INTERVAL_SECONDS)
                self.render_context.run_state.set_last_render_start_timestamp()

            self.render_context.event_bus.publish(
                RenderStateUpdated(
                    state=self.render_context.state,
                    previous_state=previous_state,
                    snapshot=self.render_context.create_snapshot(),
                )
            )
            previous_state = deepcopy(self.render_context.state)
            self.render_context.run_state.current_render_state = self.render_context.state
            self.render_context.script_execution_history.should_update_script_outputs = False

            self.render_context.previous_action_payload = previous_action_payload

            state_before = self.render_context.state
            action = self.action_map[state_before]
            action_started_at = time.monotonic()

            outcome, previous_action_payload = action.execute(self.render_context, previous_action_payload)

            trace(
                "state-machine",
                module=self.render_context.module_name,
                frid=self.render_context.frid_context.frid if self.render_context.frid_context else None,
                state=state_before,
                action=type(action).__name__,
                outcome=outcome,
                duration_s=time.monotonic() - action_started_at,
                payload_keys=(
                    sorted(previous_action_payload.keys()) if isinstance(previous_action_payload, dict) else None
                ),
            )

            if self.render_context.state == States.RENDER_FAILED.value:
                self.render_context.last_error_message = previous_action_payload
                self.render_context.event_bus.publish(RenderModuleFailed(module_name=self.render_context.module_name))
                break

            if self.render_context.state == States.RENDER_COMPLETED.value:
                self.render_context.event_bus.publish(
                    RenderModuleCompleted(module_name=self.render_context.module_name)
                )
                break

            next_trigger = self.action_result_triggers_map[outcome]
            self.machine.dispatch(next_trigger)
            trace(
                "state-machine",
                module=self.render_context.module_name,
                trigger=next_trigger,
                new_state=self.render_context.state,
            )

        self.render_context.run_state.add_to_render_time()

    def generate_render_machine_graph(self):
        """Generate a visual diagram of the state machine."""
        self.render_context.get_graph().draw("render_machine_diagram.png", prog="dot")
