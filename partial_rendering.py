from dataclasses import dataclass
from typing import Literal

import plain_spec
from plain2code_exceptions import ModuleDoesNotExistError
from plain_modules import PlainModule


@dataclass
class PartialRender:
    last_render_module: PlainModule
    last_render_frid: str | None
    change: PlainModule | None = None
    change_type: Literal["spec_change", "code_change"] | None = None


@dataclass
class PartialRenderChoice:
    module: PlainModule | None = None
    render_range: list[str] | None = None
    msg: str | None = None
    wipe_later_modules: bool = False


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


def detect_partial_rendering(plain_module: PlainModule) -> PartialRender | None:
    sc = spec_change(plain_module)
    cc = code_change(plain_module)
    all_required_modules = plain_module.all_required_modules
    last_rendered_module_name, last_rendered_frid = plain_module.get_module_render_status()
    if last_rendered_module_name is None and last_rendered_frid is None:
        return None

    if last_rendered_module_name == plain_module.module_name:
        if (
            last_rendered_frid is None
            or plain_spec.get_next_frid(plain_module.plain_source, last_rendered_frid) is not None
        ):
            module = plain_module
        else:
            return None
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

    pr = PartialRender(
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


def get_choices(
    plain_module: PlainModule,
    partial_render: PartialRender,
    force_render: bool = False,
) -> dict[str, PartialRenderChoice]:
    choices = dict[str, PartialRenderChoice]()
    choice_idx = 1

    if partial_render.last_render_module.is_initial_module():
        choices[str(choice_idx)] = PartialRenderChoice(
            module=partial_render.last_render_module,
            render_range=None,
            msg=f"Start from module [#5593FF]{partial_render.last_render_module.module_name}[/]",
            wipe_later_modules=True,
        )
        choice_idx += 1

    elif not partial_render.last_render_module.is_module_fully_rendered():
        if not partial_render.last_render_frid:
            raise ValueError("Last render FRID is not set for a non-initial module")

        next_frid, next_module = plain_module.get_next_frid(
            partial_render.last_render_frid, partial_render.last_render_module.module_name
        )
        render_range = plain_spec.get_render_range_from(next_frid, next_module.plain_source)
        msg = "Continue from"
        if next_frid != plain_spec.get_first_frid(next_module.plain_source):
            msg += f" functionality [#5593FF]{next_frid}[/]"
        else:
            msg += f" module [#5593FF]{next_module.module_name}[/]"

        choices[str(choice_idx)] = PartialRenderChoice(
            module=next_module,
            render_range=render_range if not force_render else None,
            wipe_later_modules=force_render,
            msg=msg,
        )
        choice_idx += 1

    else:
        next_module = plain_module.get_next_module(partial_render.last_render_module.module_name)
        if next_module is None:
            next_module = partial_render.last_render_module

        choices[str(choice_idx)] = PartialRenderChoice(
            module=next_module,
            render_range=None,
            msg=f"Start from module [#5593FF]{next_module.module_name}[/]",
        )
        choice_idx += 1

    if partial_render.change:
        all_affected_modules = list[str]()
        affected_module = False
        for module in plain_module.all_required_modules:
            if module.module_name == partial_render.change.module_name:
                affected_module = True
            if affected_module:
                all_affected_modules.append(module.module_name)

        if len(all_affected_modules) > 0:
            choices[str(choice_idx)] = PartialRenderChoice(
                module=partial_render.change,
                render_range=None,
                msg=f"Re-render all affected modules ([#5593FF]{', '.join(all_affected_modules)}[/])",
                wipe_later_modules=True,
            )
            choice_idx += 1

    if len(plain_module.all_required_modules) > 0:
        first_module = plain_module.all_required_modules[0]
        if first_module.module_name != partial_render.last_render_module.module_name and (
            partial_render.change is not None and first_module.module_name != partial_render.change.module_name
        ):
            choices[str(choice_idx)] = PartialRenderChoice(
                module=first_module,
                render_range=None,
                msg=f"Re-render from first module ([#5593FF]{first_module.module_name}[/])",
                wipe_later_modules=True,
            )
            choice_idx += 1

    choices[str(choice_idx)] = PartialRenderChoice(module=None, render_range=None, msg="Quit")
    print(choices)

    return choices
