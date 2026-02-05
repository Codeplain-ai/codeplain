import argparse
import os

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
        filename: str,
        render_range: list[str] | None,
        template_dirs: list[str],
        args: argparse.Namespace,
        run_state: RunState,
        event_bus: EventBus,
    ):
        self.codeplainAPI = codeplainAPI
        self.filename = filename
        self.render_range = render_range
        self.template_dirs = template_dirs
        self.args = args
        self.run_state = run_state
        self.event_bus = event_bus

    def _ensure_module_folders_exist(self, module_name: str, first_render_frid: str) -> tuple[str, str]:
        """
        Ensure that build and conformance test folders exist for the module.

        Args:
            module_name: Name of the module being rendered
            first_render_frid: The first FRID in the render range

        Returns:
            tuple[str, str]: (build_folder_path, conformance_tests_path)

        Raises:
            MissingPreviousFridCommitsError: If any required folders are missing
        """
        build_folder_path = os.path.join(self.args.build_folder, module_name)
        conformance_tests_path = os.path.join(self.args.conformance_tests_folder, module_name)

        if not os.path.exists(build_folder_path):
            raise MissingPreviousFunctionalitiesError(
                f"Cannot start rendering from functionality {first_render_frid} for module '{module_name}' because the source code folder does not exist.\n\n"
                f"To fix this, please render the module from the beginning by running:\n"
                f"  codeplain {module_name}{plain_file.PLAIN_SOURCE_FILE_EXTENSION}"
            )

        if not os.path.exists(conformance_tests_path):
            raise MissingPreviousFunctionalitiesError(
                f"Cannot start rendering from functionality {first_render_frid} for module '{module_name}' because the conformance tests folder does not exist.\n\n"
                f"To fix this, please render the module from the beginning by running:\n"
                f"  codeplain {module_name}{plain_file.PLAIN_SOURCE_FILE_EXTENSION}"
            )

        return build_folder_path, conformance_tests_path

    def _ensure_frid_commit_exists(
        self,
        frid: str,
        module_name: str,
        build_folder_path: str,
        conformance_tests_path: str,
        first_render_frid: str,
    ) -> None:
        """
        Ensure commit exists for a single FRID in both repositories.

        Args:
            frid: The FRID to check
            module_name: Name of the module
            build_folder_path: Path to the build folder
            conformance_tests_path: Path to the conformance tests folder
            first_render_frid: The first FRID in the render range (for error messages)

        Raises:
            MissingPreviousFridCommitsError: If the commit is missing
        """
        # Check in build folder
        if not git_utils.has_commit_for_frid(build_folder_path, frid, module_name):
            raise MissingPreviousFunctionalitiesError(
                f"Cannot start rendering from functionality {first_render_frid} for module '{module_name}' because the implementation of the previous functionality ({frid}) hasn't been completed yet.\n\n"
                f"To fix this, please render the missing functionality ({frid}) first by running:\n"
                f"  codeplain {module_name}{plain_file.PLAIN_SOURCE_FILE_EXTENSION} --render-from {frid}"
            )

        # Check in conformance tests folder (only if conformance tests are enabled)
        if self.args.render_conformance_tests:
            if not git_utils.has_commit_for_frid(conformance_tests_path, frid, module_name):
                raise MissingPreviousFunctionalitiesError(
                    f"Cannot start rendering from functionality {first_render_frid} for module '{module_name}' because the conformance tests for the previous functionality ({frid}) haven't been completed yet.\n\n"
                    f"To fix this, please render the missing functionality ({frid}) first by running:\n"
                    f"  codeplain {module_name}{plain_file.PLAIN_SOURCE_FILE_EXTENSION} --render-from {frid}"
                )

    def _ensure_previous_frid_commits_exist(
        self, module_name: str, plain_source: dict, render_range: list[str]
    ) -> None:
        """
        Ensure that all FRID commits before the render_range exist.

        This is a precondition check that must pass before rendering can proceed.
        Raises an exception if any previous FRID commits are missing.

        Args:
            module_name: Name of the module being rendered
            plain_source: The plain source tree
            render_range: List of FRIDs to render

        Raises:
            MissingPreviousFridCommitsError: If any previous FRID commits are missing
        """
        first_render_frid = render_range[0]

        # Get all FRIDs that should have been rendered before this one
        previous_frids = plain_spec.get_frids_before(plain_source, first_render_frid)
        if not previous_frids:
            return

        # Ensure the module folders exist
        build_folder_path, conformance_tests_path = self._ensure_module_folders_exist(module_name, first_render_frid)

        # Verify commits exist for all previous FRIDs
        for prev_frid in previous_frids:
            self._ensure_frid_commit_exists(
                prev_frid,
                module_name,
                build_folder_path,
                conformance_tests_path,
                first_render_frid,
            )

    def _build_render_context_for_module(
        self,
        module_name: str,
        memory_manager: MemoryManager,
        plain_source: dict,
        required_modules: list[PlainModule],
        template_dirs: list[str],
        render_range: list[str] | None,
    ) -> RenderContext:
        return RenderContext(
            self.codeplainAPI,
            memory_manager,
            module_name,
            plain_source,
            required_modules,
            template_dirs,
            build_folder=os.path.join(self.args.build_folder, module_name),
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
        )

    def _render_module(
        self, filename: str, render_range: list[str] | None, force_render: bool
    ) -> tuple[bool, list[PlainModule], bool]:
        """Render a module.

        Returns:
            tuple[bool, list[PlainModule], bool]: (Whether the module was rendered, the required modules, and whether the rendering failed)
        """
        module_name, plain_source, required_modules_list = plain_file.plain_file_parser(filename, self.template_dirs)

        resources_list = []
        plain_spec.collect_linked_resources(plain_source, resources_list, None, True)

        # Ensure that all previous FRID commits exist before proceeding with render_range
        if render_range is not None:
            self._ensure_previous_frid_commits_exist(module_name, plain_source, render_range)

        required_modules = []
        has_any_required_module_changed = False
        if not self.args.render_machine_graph and required_modules_list:
            console.info(f"Analyzing required modules of module {module_name}...")
            for required_module_name in required_modules_list:
                required_module_filename = required_module_name + plain_file.PLAIN_SOURCE_FILE_EXTENSION
                has_module_changed, sub_required_modules, rendering_failed = self._render_module(
                    required_module_filename,
                    None,
                    self.args.force_render,
                )

                if rendering_failed:
                    return False, required_modules, True

                if has_module_changed:
                    has_any_required_module_changed = True

                for sub_required_module in sub_required_modules:
                    if sub_required_module.name not in [m.name for m in required_modules]:
                        required_modules.append(
                            plain_modules.PlainModule(sub_required_module.name, self.args.build_folder)
                        )

                required_modules.append(plain_modules.PlainModule(required_module_name, self.args.build_folder))

        plain_module = plain_modules.PlainModule(module_name, self.args.build_folder)
        if (
            ((not force_render) or any(module.name == plain_module.name for module in self.loaded_modules))
            and plain_module.get_repo() is not None
            and not plain_module.has_plain_spec_changed(plain_source, resources_list)
            and not plain_module.has_required_modules_code_changed(required_modules)
            and not has_any_required_module_changed
        ):
            return False, required_modules, False

        memory_manager = MemoryManager(self.codeplainAPI, os.path.join(self.args.build_folder, module_name))
        render_context = self._build_render_context_for_module(
            module_name, memory_manager, plain_source, required_modules, self.template_dirs, render_range
        )

        code_renderer = CodeRenderer(render_context)
        if self.args.render_machine_graph:
            code_renderer.generate_render_machine_graph()
            return True, required_modules, False

        code_renderer.run()
        if code_renderer.render_context.state == States.RENDER_FAILED.value:
            error_message = RenderError.get_display_message(
                code_renderer.render_context.previous_action_payload,
                fallback_message=code_renderer.render_context.last_error_message,
            )
            code_renderer.render_context.event_bus.publish(RenderFailed(error_message=error_message))
            return False, required_modules, True

        plain_module.save_module_metadata(plain_source, resources_list, required_modules)

        self.loaded_modules.append(plain_module)

        return True, required_modules, False

    def render_module(self) -> None:
        self.loaded_modules = list[PlainModule]()
        _, _, rendering_failed = self._render_module(self.filename, self.render_range, True)
        if not rendering_failed:
            # Get the last module that completed rendering
            if self.args.copy_build:
                rendered_code_path = f"{self.args.build_dest}/"
            else:
                last_module_name = self.filename.replace(plain_file.PLAIN_SOURCE_FILE_EXTENSION, "")
                rendered_code_path = f"{os.path.join(self.args.build_folder, last_module_name)}/"

            self.event_bus.publish(RenderCompleted(rendered_code_path=rendered_code_path))
