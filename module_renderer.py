from __future__ import annotations

import argparse
import os
import threading
from dataclasses import dataclass
from typing import Callable

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


@dataclass
class ModuleRenderInfo:
    module_name: str
    change_reasons: list[str]
    all_frids: list[str]
    implemented_frids: list[str]
    no_prior_render: bool


@dataclass
class _ModuleTraversalResult:
    """State produced by _traverse_module_tree, shared by rendering and plan collection."""

    module_name: str
    plain_source: dict
    resources_list: list[dict]
    required_modules: list[PlainModule]
    has_any_required_module_changed: bool
    changed_required_module_name: str | None
    plain_module: PlainModule
    no_prior_render: bool
    spec_changed: bool
    required_code_changed: bool
    should_skip: bool
    rendering_failed: bool


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
        stop_event: threading.Event | None = None,
        enter_pause_event: threading.Event | None = None,
    ):
        self.codeplainAPI = codeplainAPI
        self.filename = filename
        self.render_range = render_range
        self.template_dirs = template_dirs
        self.args = args
        self.run_state = run_state
        self.event_bus = event_bus
        self.stop_event = stop_event
        self.enter_pause_event = enter_pause_event
        self._render_plan: list[ModuleRenderInfo] = []
        self._dry_run_processed: set[str] = set()
        self._skip_required_rerender: bool = False

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
            test_script_timeout=self.args.test_script_timeout,
            stop_event=self.stop_event,
            enter_pause_event=self.enter_pause_event,
        )

    def _get_required_modules_without_rendering(self, filename: str) -> list[PlainModule]:
        """Parse module and collect all transitive required modules without rendering."""
        module_name, _, required_modules_list = plain_file.plain_file_parser(filename, self.template_dirs)

        result = []
        for req_name in required_modules_list:
            sub = self._get_required_modules_without_rendering(req_name + plain_file.PLAIN_SOURCE_FILE_EXTENSION)
            for m in sub:
                if m.name not in [r.name for r in result]:
                    result.append(m)
            result.append(plain_modules.PlainModule(req_name, self.args.build_folder))

        return result

    def _traverse_module_tree(
        self,
        filename: str,
        render_range: list[str] | None,
        force_render: bool,
        required_module_fn: Callable[[str], tuple[bool, list[PlainModule], bool]],
        already_processed_fn: Callable[[str], bool],
        validate_commits: bool = True,
        log_progress: bool = True,
    ) -> _ModuleTraversalResult:
        """Parse a module, process its required modules, and evaluate the skip condition.

        This is the shared traversal logic used by both _render_module and
        _collect_render_plan_module. Callers supply:
          - required_module_fn: what to do for each required module (render vs. collect)
          - already_processed_fn: how to check if a module was already handled this run

        Args:
            filename: Plain file to process.
            render_range: FRIDs to render (used only for commit validation).
            force_render: Whether to force rendering even if nothing changed.
            required_module_fn: Called for each required module filename; returns
                (has_changed, sub_required_modules, rendering_failed).
            already_processed_fn: Called with module_name; returns True if already handled.
            validate_commits: Whether to validate prior FRID commits against render_range.
            log_progress: Whether to emit debug logs while processing required modules.

        Returns:
            _ModuleTraversalResult with all state needed by the caller, including
            should_skip and rendering_failed flags.
        """
        module_name, plain_source, required_modules_list = plain_file.plain_file_parser(filename, self.template_dirs)

        resources_list = []
        plain_spec.collect_linked_resources(plain_source, resources_list, None, True)

        if render_range is not None and validate_commits:
            self._ensure_previous_frid_commits_exist(module_name, plain_source, render_range)

        required_modules: list[PlainModule] = []
        has_any_required_module_changed = False
        changed_required_module_name = None

        if not self.args.render_machine_graph and required_modules_list:
            if log_progress:
                console.debug(f"Analyzing required modules of module {module_name}...")
            for required_module_name in required_modules_list:
                required_module_filename = required_module_name + plain_file.PLAIN_SOURCE_FILE_EXTENSION
                has_module_changed, sub_required_modules, rendering_failed = required_module_fn(
                    required_module_filename
                )

                if rendering_failed:
                    plain_module = plain_modules.PlainModule(module_name, self.args.build_folder)
                    return _ModuleTraversalResult(
                        module_name=module_name,
                        plain_source=plain_source,
                        resources_list=resources_list,
                        required_modules=required_modules,
                        has_any_required_module_changed=has_any_required_module_changed,
                        changed_required_module_name=changed_required_module_name,
                        plain_module=plain_module,
                        no_prior_render=False,
                        spec_changed=False,
                        required_code_changed=False,
                        should_skip=False,
                        rendering_failed=True,
                    )

                if has_module_changed:
                    has_any_required_module_changed = True
                    if changed_required_module_name is None:
                        changed_required_module_name = required_module_name

                for sub in sub_required_modules:
                    if sub.name not in [m.name for m in required_modules]:
                        required_modules.append(plain_modules.PlainModule(sub.name, self.args.build_folder))

                required_modules.append(plain_modules.PlainModule(required_module_name, self.args.build_folder))

        plain_module = plain_modules.PlainModule(module_name, self.args.build_folder)
        no_prior_render = plain_module.get_repo() is None
        spec_changed = not no_prior_render and plain_module.has_plain_spec_changed(plain_source, resources_list)
        required_code_changed = not no_prior_render and plain_module.has_required_modules_code_changed(required_modules)
        already_processed = already_processed_fn(module_name)

        should_skip = (
            ((not force_render) or already_processed)
            and not no_prior_render
            and not spec_changed
            and not required_code_changed
            and not has_any_required_module_changed
        )

        return _ModuleTraversalResult(
            module_name=module_name,
            plain_source=plain_source,
            resources_list=resources_list,
            required_modules=required_modules,
            has_any_required_module_changed=has_any_required_module_changed,
            changed_required_module_name=changed_required_module_name,
            plain_module=plain_module,
            no_prior_render=no_prior_render,
            spec_changed=spec_changed,
            required_code_changed=required_code_changed,
            should_skip=should_skip,
            rendering_failed=False,
        )

    def _render_module(
        self, filename: str, render_range: list[str] | None, force_render: bool
    ) -> tuple[bool, list[PlainModule], bool]:
        """Render a module and all its required modules recursively.

        Returns:
            tuple[bool, list[PlainModule], bool]: (Whether the module was rendered,
                the required modules, and whether the rendering failed)
        """

        def required_module_fn(rf: str) -> tuple[bool, list[PlainModule], bool]:
            if self._skip_required_rerender:
                return False, self._get_required_modules_without_rendering(rf), False
            return self._render_module(rf, None, self.args.force_render)

        already_processed_fn = lambda name: any(m.name == name for m in self.loaded_modules)

        state = self._traverse_module_tree(
            filename,
            render_range,
            force_render,
            required_module_fn,
            already_processed_fn,
            validate_commits=True,
            log_progress=True,
        )

        if state.rendering_failed:
            return False, state.required_modules, True

        if state.should_skip:
            return False, state.required_modules, False

        memory_manager = MemoryManager(self.codeplainAPI, os.path.join(self.args.build_folder, state.module_name))
        render_context = self._build_render_context_for_module(
            state.module_name,
            memory_manager,
            state.plain_source,
            state.required_modules,
            self.template_dirs,
            render_range,
        )

        code_renderer = CodeRenderer(render_context)
        if self.args.render_machine_graph:
            code_renderer.generate_render_machine_graph()
            return True, state.required_modules, False

        code_renderer.run()
        if code_renderer.render_context.state == States.RENDER_FAILED.value:
            error_message = RenderError.get_display_message(
                code_renderer.render_context.previous_action_payload,
                fallback_message=code_renderer.render_context.last_error_message,
            )
            code_renderer.render_context.event_bus.publish(RenderFailed(error_message=error_message))
            return False, state.required_modules, True

        state.plain_module.save_module_metadata(state.plain_source, state.resources_list, state.required_modules)
        self.loaded_modules.append(state.plain_module)

        return True, state.required_modules, False

    def _collect_render_plan_module(self, filename: str, force_render: bool) -> tuple[bool, list[PlainModule], bool]:
        """Collect render plan info for a module without rendering anything.

        Appends a ModuleRenderInfo to self._render_plan for each module that
        would be rendered. Returns the same (has_changed, required_modules, failed)
        tuple shape as _render_module so it can be used as a required_module_fn.
        """
        required_module_fn = lambda rf: self._collect_render_plan_module(rf, self.args.force_render)
        already_processed_fn = lambda name: name in self._dry_run_processed

        state = self._traverse_module_tree(
            filename,
            None,
            force_render,
            required_module_fn,
            already_processed_fn,
            validate_commits=False,
            log_progress=False,
        )

        if state.rendering_failed or state.should_skip:
            return False, state.required_modules, state.rendering_failed

        all_frids = list(plain_spec.get_frids(state.plain_source))

        if state.no_prior_render:
            implemented_frids = []
        else:
            build_folder_path = os.path.join(self.args.build_folder, state.module_name)
            implemented_frids = [
                frid for frid in all_frids if git_utils.has_commit_for_frid(build_folder_path, frid, state.module_name)
            ]

        change_reasons = []
        if state.spec_changed:
            change_reasons.append("spec changed")
        if state.required_code_changed or state.has_any_required_module_changed:
            if state.changed_required_module_name:
                change_reasons.append(f"required module '{state.changed_required_module_name}' changed")
            else:
                change_reasons.append("required module changed")

        self._render_plan.append(
            ModuleRenderInfo(
                module_name=state.module_name,
                change_reasons=change_reasons,
                all_frids=all_frids,
                implemented_frids=implemented_frids,
                no_prior_render=state.no_prior_render,
            )
        )
        self._dry_run_processed.add(state.module_name)

        return True, state.required_modules, False

    def collect_render_plan(self) -> None:
        """Dry-run traversal to populate self._render_plan without rendering anything."""
        self._render_plan = []
        self._dry_run_processed = set()
        self._collect_render_plan_module(self.filename, True)

    def prompt_user_if_needed(self) -> bool:
        """Prompt the user about re-rendering choices if relevant.

        Must be called on the main thread before the TUI is started.

        Returns:
            True if the user cancelled, False otherwise.
        """
        render_plan = self._render_plan

        if not render_plan:
            return False

        current = render_plan[-1]
        required_in_plan = render_plan[:-1]

        has_spec_changes = any("spec changed" in m.change_reasons for m in render_plan)
        has_required_changes = len(required_in_plan) > 0

        # Situation 11: fresh render with no prior state — no prompt needed
        if not has_spec_changes and not has_required_changes and current.no_prior_render:
            return False

        impl = current.implemented_frids
        all_f = current.all_frids
        no_prior = current.no_prior_render
        is_partial = not no_prior and 0 < len(impl) < len(all_f)
        is_full = not no_prior and len(all_f) > 0 and len(impl) == len(all_f)

        first_unimplemented = None
        if is_partial and all_f:
            impl_set = set(impl)
            first_unimplemented = next((f for f in all_f if f not in impl_set), None)

        spec_changed_modules = [m for m in render_plan if "spec changed" in m.change_reasons]
        all_module_names = [m.module_name for m in render_plan]
        cli_render_range_set = self.render_range is not None

        # --- Build message ---
        if spec_changed_modules:
            names = " and ".join(m.module_name for m in spec_changed_modules)
            print(f"\nChanges in specs in {names} have been identified.")

        if has_spec_changes or has_required_changes:
            print("This would require re-rendering of the following modules:\n")
            for m in required_in_plan:
                print(f"  {m.module_name}")

            if no_prior:
                annotation = "(not yet rendered)"
            elif is_full:
                annotation = "(all functionalities were already implemented)"
            elif is_partial:
                annotation = f"(functionalities {', '.join(impl)} were already implemented)"
            else:
                annotation = ""

            if annotation:
                print(f"  {current.module_name}    {annotation}")
            else:
                print(f"  {current.module_name}")
        else:
            # Situations 12/13: no spec changes, module partially or fully rendered
            if is_partial:
                print(
                    f"\n{current.module_name} has been partially rendered (functionalities {', '.join(impl)} were already implemented)."
                )
            elif is_full:
                print(f"\nAll functionalities in {current.module_name} were already implemented.")
            else:
                return False

        print()

        # --- Build choices ---
        choices: dict[str, tuple[str, str | None]] = {}

        if has_required_changes and no_prior:
            # Situation 9
            print(f"[a] Re-render all ({', '.join(all_module_names)})")
            print(f"[b] Render {current.module_name}")
            print("[c] Cancel")
            choices = {
                "a": ("rerender_all", None),
                "b": ("rerender_current", None),
                "c": ("cancel", None),
            }

        elif has_required_changes and is_full:
            # Situations 2 / 6 / 8
            print(f"[a] Re-render all ({', '.join(all_module_names)})")
            print(f"[b] Re-render {current.module_name} from scratch")
            print("[c] Cancel")
            choices = {
                "a": ("rerender_all", None),
                "b": ("rerender_current", None),
                "c": ("cancel", None),
            }

        elif has_required_changes and is_partial:
            # Situations 1 / 5 / 7
            print(f"[a] Re-render all ({', '.join(all_module_names)})")
            if not cli_render_range_set and first_unimplemented:
                print(f"[b] Continue from functionality {first_unimplemented} ({current.module_name} only)")
                print(f"[c] Re-render {current.module_name} from scratch")
                print("[d] Cancel")
                choices = {
                    "a": ("rerender_all", None),
                    "b": ("continue_from", first_unimplemented),
                    "c": ("rerender_current", None),
                    "d": ("cancel", None),
                }
            else:
                print(f"[b] Re-render {current.module_name} from scratch")
                print("[c] Cancel")
                choices = {
                    "a": ("rerender_all", None),
                    "b": ("rerender_current", None),
                    "c": ("cancel", None),
                }

        elif not has_required_changes and "spec changed" in current.change_reasons and is_full:
            # Situation 4
            print(f"[a] Re-render ({current.module_name})")
            print("[b] Cancel")
            choices = {"a": ("rerender_all", None), "b": ("cancel", None)}

        elif not has_required_changes and "spec changed" in current.change_reasons and is_partial:
            # Situation 3
            print(f"[a] Re-render all ({current.module_name})")
            if not cli_render_range_set and first_unimplemented:
                print(f"[b] Continue from functionality {first_unimplemented}")
                print("[c] Cancel")
                choices = {
                    "a": ("rerender_all", None),
                    "b": ("continue_from", first_unimplemented),
                    "c": ("cancel", None),
                }
            else:
                print("[b] Cancel")
                choices = {"a": ("rerender_all", None), "b": ("cancel", None)}

        elif is_full and not has_spec_changes:
            # Situation 13
            print(f"[a] Re-render {current.module_name} from scratch")
            print("[b] Cancel")
            choices = {"a": ("rerender_all", None), "b": ("cancel", None)}

        elif is_partial and not has_spec_changes:
            # Situation 12
            print(f"[a] Re-render {current.module_name} from scratch")
            if not cli_render_range_set and first_unimplemented:
                print(f"[b] Continue from functionality {first_unimplemented}")
                print("[c] Cancel")
                choices = {
                    "a": ("rerender_all", None),
                    "b": ("continue_from", first_unimplemented),
                    "c": ("cancel", None),
                }
            else:
                print("[b] Cancel")
                choices = {"a": ("rerender_all", None), "b": ("cancel", None)}

        else:
            return False

        # --- Get user input ---
        print()
        while True:
            try:
                raw = input("Your choice: ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                return True

            if raw in choices:
                decision = choices[raw]
                break
            print(f"Please enter one of: {', '.join(choices.keys())}")

        if decision[0] == "cancel":
            return True

        if decision[0] == "continue_from":
            frid = decision[1]
            start_idx = current.all_frids.index(frid)
            self.render_range = current.all_frids[start_idx:]
            self._skip_required_rerender = True
        elif decision[0] == "rerender_current":
            self.render_range = None
            self._skip_required_rerender = True
        # rerender_all: keep existing render_range, _skip_required_rerender stays False

        return False

    def render_module(self) -> None:
        self.loaded_modules = list[PlainModule]()
        self._skip_required_rerender = False
        _, _, rendering_failed = self._render_module(self.filename, self.render_range, True)
        if not rendering_failed:
            # Get the last module that completed rendering
            if self.args.copy_build:
                rendered_code_path = f"{self.args.build_dest}/"
            else:
                last_module_name = self.filename.replace(plain_file.PLAIN_SOURCE_FILE_EXTENSION, "")
                rendered_code_path = f"{os.path.join(self.args.build_folder, last_module_name)}/"

            self.event_bus.publish(RenderCompleted(rendered_code_path=rendered_code_path))
