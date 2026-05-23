from dataclasses import dataclass
from typing import Literal

import plain_spec
from plain2code_exceptions import ModuleDoesNotExistError
from plain_modules import PlainModule


@dataclass
class PlainModuleRenderState:
    last_render_module: PlainModule
    last_render_frid: str | None
    change: PlainModule | None = None
    change_type: Literal["spec_change", "code_change"] | None = None


@dataclass
class RenderChoice:
    module: PlainModule | None = None
    render_range: list[str] | None = None
    wipe_later_modules: bool = False
    is_destructive: bool = False
    choice_type: str | None = None


def spec_change(plain_module: PlainModule) -> PlainModule | None:
    all_modules = plain_module.all_required_modules + [plain_module]
    for _module in all_modules:
        module_metadata = _module.load_module_metadata()
        if (
            module_metadata
            and "source_hash" in module_metadata
            and module_metadata["source_hash"] != _module.get_module_source_hash()
        ):
            return _module

    return None


def code_change(plain_module: PlainModule) -> PlainModule | None:
    all_modules = plain_module.all_required_modules + [plain_module]
    for _module in all_modules:
        if len(_module.required_modules) == 0:
            continue

        module_metadata = _module.load_module_metadata()
        previous_module = _module.required_modules[-1]
        if (
            module_metadata
            and "required_modules_code_hash" in module_metadata
            and module_metadata["required_modules_code_hash"] != previous_module.get_module_code_hash()
        ):
            return previous_module

    return None


def module_comes_before_or_equal(
    all_required_modules: list[PlainModule],
    module1: PlainModule,
    module2: PlainModule,
) -> bool:
    for module in all_required_modules:
        if module.module_name == module1.module_name:
            return True
        if module.module_name == module2.module_name:
            return False

    raise ValueError(f"Module {module1.module_name} and {module2.module_name} not found in {all_required_modules}")


def get_plain_module_render_state(plain_module: PlainModule) -> PlainModuleRenderState | None:
    sc = spec_change(plain_module)
    cc = code_change(plain_module)
    all_required_modules = plain_module.all_required_modules
    last_rendered_module_name, last_rendered_frid = plain_module.get_module_render_status()
    if last_rendered_module_name is None and last_rendered_frid is None:
        return None

    if last_rendered_module_name == plain_module.module_name:
        module = plain_module
    else:
        found_module: PlainModule | None = None
        for required_module in all_required_modules:
            if required_module.module_name == last_rendered_module_name:
                found_module = required_module

        if found_module is None:
            raise ModuleDoesNotExistError(
                f"Last rendered module {last_rendered_module_name} not found in {[rmodule.module_name for rmodule in all_required_modules]}"
            )
        module = found_module

    pr = PlainModuleRenderState(
        last_render_module=module,
        last_render_frid=last_rendered_frid,
        change=None,
        change_type=None,
    )

    if sc is None and cc is None:
        return pr

    if sc is not None:
        pr.change = sc
        pr.change_type = "spec_change"

    if cc is not None and (
        pr.change is None
        or (pr.change is not None and module_comes_before_or_equal(all_required_modules, cc, pr.change))
    ):
        pr.change = cc
        pr.change_type = "code_change"

    return pr


def get_all_affected_modules_from_change(
    plain_module: PlainModule,
    plain_module_render_state: PlainModuleRenderState,
) -> list[PlainModule]:
    all_affected_modules = dict[str, PlainModule]()

    if plain_module_render_state.change_type == "spec_change":
        start_module = plain_module_render_state.change
    elif plain_module_render_state.change_type == "code_change":
        if plain_module_render_state.change.is_module_fully_rendered():
            start_module = plain_module.get_next_module(plain_module_render_state.change.module_name)
        else:
            start_module = plain_module_render_state.change
    else:
        raise ValueError(f"Unknown change type: {plain_module_render_state.change_type}")

    affected_module = False
    all_modules = plain_module.all_required_modules + [plain_module]
    for module in all_modules:
        if module.module_name == start_module.module_name:
            affected_module = True

        if affected_module and module.module_name not in all_affected_modules:
            all_affected_modules[module.module_name] = module

    return list(all_affected_modules.values())


def get_render_choices(
    plain_module: PlainModule,
    plain_module_render_state: PlainModuleRenderState,
    force_render: bool = False,
) -> dict[str, RenderChoice]:
    choices = dict[str, RenderChoice]()
    choice_idx = 1

    if plain_module_render_state.last_render_module.is_initial_module():
        choices[str(choice_idx)] = RenderChoice(
            module=plain_module_render_state.last_render_module,
            render_range=None,
            choice_type="module_start",
            wipe_later_modules=True,
            is_destructive=False,
        )
        choice_idx += 1

    elif not plain_module_render_state.last_render_module.is_module_fully_rendered():
        if not plain_module_render_state.last_render_frid:
            raise ValueError("Last render FRID is not set for a non-initial module")

        next_frid, next_module = plain_module.get_next_frid(
            plain_module_render_state.last_render_frid, plain_module_render_state.last_render_module.module_name
        )
        render_range = plain_spec.get_render_range_from(next_frid, next_module.plain_source)

        choices[str(choice_idx)] = RenderChoice(
            module=next_module,
            render_range=render_range if not force_render else None,
            wipe_later_modules=force_render,
            choice_type="continue_from_frid",
        )
        choice_idx += 1

    else:
        next_module = plain_module.get_next_module(plain_module_render_state.last_render_module.module_name)
        if next_module is None:
            next_module = plain_module_render_state.last_render_module

        choices[str(choice_idx)] = RenderChoice(
            module=next_module,
            render_range=None,
            is_destructive=plain_module_render_state.last_render_module.module_name == plain_module.module_name,
            choice_type="module_start",
        )
        choice_idx += 1

    if plain_module_render_state.change:
        all_affected_modules = get_all_affected_modules_from_change(plain_module, plain_module_render_state)

        if len(all_affected_modules) > 0:
            choices[str(choice_idx)] = RenderChoice(
                module=all_affected_modules[0],
                render_range=None,
                choice_type="rerender_affected",
                wipe_later_modules=True,
                is_destructive=True,
            )
            choice_idx += 1

    if len(plain_module.all_required_modules) > 0:
        first_module = plain_module.all_required_modules[0]
        if first_module.module_name != plain_module_render_state.last_render_module.module_name and (
            plain_module_render_state.change is not None
            and first_module.module_name != plain_module_render_state.change.module_name
        ):
            choices[str(choice_idx)] = RenderChoice(
                module=first_module,
                render_range=None,
                choice_type="rerender_from_first",
                wipe_later_modules=True,
                is_destructive=True,
            )
            choice_idx += 1

    choices[str(choice_idx)] = RenderChoice(module=None, render_range=None, choice_type="quit")
    return choices
