import argparse
import os
import threading

import git_utils
import plain_file
import plain_modules
import plain_spec
from event_bus import EventBus
from memory_management import MemoryManager
from plain2code_console import console
from plain2code_events import RenderCompleted, RenderFailed
from plain2code_exceptions import MissingPreviousFunctionalitiesError
from plain2code_state import RunState
from plain_modules import PlainModule
from render_machine.code_renderer import CodeRenderer
from render_machine.render_context import RenderContext
from render_machine.render_types import RenderError
from render_machine.states import States


class ModuleRenderer:
    def __init__(
        self,
        codeplainAPI,
        plain_module: PlainModule,
        render_range: list[str] | None,
        args: argparse.Namespace,
        run_state: RunState,
        event_bus: EventBus,
        stop_event: threading.Event | None = None,
        enter_pause_event: threading.Event | None = None,
    ):
        self.codeplainAPI = codeplainAPI
        self.plain_module = plain_module
        self.render_range = render_range
        self.args = args
        self.run_state = run_state
        self.event_bus = event_bus
        self.stop_event = stop_event
        self.enter_pause_event = enter_pause_event

    def _build_render_context_for_module(
        self,
        plain_module: PlainModule,
        memory_manager: MemoryManager,
        render_range: list[str] | None,
    ) -> RenderContext:
        return RenderContext(
            self.codeplainAPI,
            memory_manager,
            plain_module.module_name,
            plain_module.plain_source,
            plain_module.all_required_modules,
            plain_module.template_dirs,
            build_folder=os.path.join(self.args.build_folder, plain_module.module_name),
            build_dest=self.args.build_dest,
            conformance_tests_folder=self.args.conformance_tests_folder,
            conformance_tests_dest=self.args.conformance_tests_dest,
            unittests_script=self.args.unittests_script,
            conformance_tests_script=self.args.conformance_tests_script,
            prepare_environment_script=self.args.prepare_environment_script,
            copy_build=self.args.copy_build,
            copy_conformance_tests=self.args.copy_conformance_tests,
            render_range=render_range,
            render_conformance_tests=self.args.render_conformance_tests,
            base_folder=self.args.base_folder,
            verbose=self.args.verbose,
            run_state=self.run_state,
            event_bus=self.event_bus,
            test_script_timeout=self.args.test_script_timeout,
            stop_event=self.stop_event,
            enter_pause_event=self.enter_pause_event,
        )

    def _render_module(
        self, plain_module: PlainModule, render_range: list[str] | None, force_render: bool
    ) -> tuple[bool, list[PlainModule], bool]:
        """Render a module.

        Returns:
            tuple[bool, bool]: (Whether the module was rendered and whether the rendering failed)
        """
        if render_range is not None:
            plain_module.ensure_previous_frid_commits_exist(render_range)

        has_any_required_module_changed = False
        if not self.args.render_machine_graph and plain_module.required_modules:
            console.debug(f"Analyzing required modules of module {plain_module.module_name}...")
            for required_module in plain_module.required_modules:
                has_any_required_module_changed, rendering_failed = self._render_module(
                    required_module,
                    None,
                    self.args.force_render,
                )

                if rendering_failed:
                    return False, True

        if not (
            force_render
            or any(module.filename == plain_module.filename for module in self.loaded_modules)
            or plain_module.get_repo() is None
            or plain_module.has_plain_spec_changed()
            or plain_module.has_required_modules_code_changed()
            or has_any_required_module_changed
        ):
            return False, False

        memory_manager = MemoryManager(
            self.codeplainAPI,
            os.path.join(
                self.args.build_folder,
                plain_module.module_name,
            ),
        )
        render_context = self._build_render_context_for_module(
            plain_module,
            memory_manager,
            render_range,
        )

        code_renderer = CodeRenderer(render_context)
        if self.args.render_machine_graph:
            code_renderer.generate_render_machine_graph()
            return True, False

        code_renderer.run()
        if code_renderer.render_context.state == States.RENDER_FAILED.value:
            error_message = RenderError.get_display_message(
                code_renderer.render_context.previous_action_payload,
                fallback_message=code_renderer.render_context.last_error_message,
            )
            code_renderer.render_context.event_bus.publish(RenderFailed(error_message=error_message))
            return False, True

        plain_module.save_module_metadata()

        self.loaded_modules.append(plain_module)

        return True, False

    def render_module(self) -> None:
        self.loaded_modules = list[PlainModule]()
        _, rendering_failed = self._render_module(self.plain_module, self.render_range, True)
        if not rendering_failed:
            # Get the last module that completed rendering
            if self.args.copy_build:
                rendered_code_path = f"{self.args.build_dest}/"
            else:
                rendered_code_path = f"{os.path.join(self.args.build_folder, self.plain_module.module_name)}/"

            self.run_state.set_render_generated_code_path(rendered_code_path)
            self.event_bus.publish(RenderCompleted(rendered_code_path=rendered_code_path))
