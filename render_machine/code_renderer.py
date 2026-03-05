from copy import deepcopy

from transitions.extensions.diagrams import HierarchicalGraphMachine

from plain2code_events import RenderModuleCompleted, RenderModuleStarted, RenderStateUpdated
from render_machine.render_context import RenderContext
from render_machine.state_machine_config import StateMachineConfig, States


class CodeRenderer:
    """Main code renderer class that orchestrates the code generation workflow using a hierarchical state machine."""

    def __init__(self, render_context: RenderContext):
        self.render_context = render_context
        self.state_machine_config = StateMachineConfig()

        # Initialize the state machine
        states = self.state_machine_config.get_states(self.render_context)
        transitions = self.state_machine_config.get_transitions()

        self.machine = HierarchicalGraphMachine(
            model=self.render_context,
            states=states,
            transitions=transitions,
            initial=States.RENDER_INITIALISED.value,
        )
        self.render_context.set_machine(self.machine)

        # Get action mappings
        self.action_map = self.state_machine_config.get_action_map()
        self.action_result_triggers_map = self.state_machine_config.get_action_result_triggers_map()

    def run(self):
        """Execute the main rendering workflow."""
        self.render_context.event_bus.publish(RenderModuleStarted(module_name=self.render_context.module_name))
        previous_action_payload = None
        previous_state = None
        while True:
            self.render_context.event_bus.publish(
                RenderStateUpdated(
                    state=self.render_context.state,
                    previous_state=previous_state,
                    snapshot=self.render_context.create_snapshot(),
                )
            )
            previous_state = deepcopy(self.render_context.state)
            self.render_context.previous_state = previous_state
            self.render_context.script_execution_history.should_update_script_outputs = False
            # Reset error message at start of each iteration to prevent stale data
            self.render_context.last_error_message = None

            outcome, previous_action_payload = self.action_map[self.render_context.state].execute(
                self.render_context, previous_action_payload
            )
            self.render_context.previous_action_payload = previous_action_payload

            if self.render_context.state in [
                States.RENDER_FAILED.value,
                States.RENDER_COMPLETED.value,
            ]:
                self.render_context.event_bus.publish(RenderModuleCompleted())
                break

            next_trigger = self.action_result_triggers_map[outcome]
            self.machine.dispatch(next_trigger)

    def generate_render_machine_graph(self):
        """Generate a visual diagram of the state machine."""
        self.render_context.get_graph().draw("render_machine_diagram.png", prog="dot")
