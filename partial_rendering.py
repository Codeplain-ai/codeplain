from dataclasses import dataclass
from typing import Literal

import plain_spec
from change_detection import PartialRenderStart, determine_partial_render_start
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


def change_is_only_future_work(
    plain_module: PlainModule,
    plain_module_render_state: PlainModuleRenderState,
    partial_start: "PartialRenderStart | None",
) -> bool:
    """Return True when a spec change only adds work *after* the last-rendered functionality.

    Appending a new functionality past the render frontier (e.g. a new functionality at the
    end of the last module) does not affect anything already rendered, so it needs no user
    decision. The normal "continue" path renders the outstanding functionalities, starting
    from the first one that was never rendered.
    """
    if partial_start is None:
        return False

    all_modules = plain_module.all_required_modules + [plain_module]
    order = {module.module_name: index for index, module in enumerate(all_modules)}
    last_index = order[plain_module_render_state.last_render_module.module_name]
    start_index = order[partial_start.module.module_name]

    if start_index != last_index:
        return start_index > last_index

    if plain_module_render_state.last_render_frid is None:
        return True

    return int(partial_start.frid) > int(plain_module_render_state.last_render_frid)


def _resume_render_choice(
    plain_module: PlainModule,
    plain_module_render_state: PlainModuleRenderState,
    force_render: bool,
) -> RenderChoice:
    """The natural next step from the last-rendered position when no decision is needed:
    start an unrendered module, continue a partially-rendered one, or advance to the next.
    """
    pr = plain_module_render_state

    if pr.last_render_module.has_no_rendered_functionality():
        return RenderChoice(
            module=pr.last_render_module,
            render_range=None,
            choice_type="module_start",
            wipe_later_modules=True,
            is_destructive=False,
        )

    if not pr.last_render_module.is_module_fully_rendered():
        if not pr.last_render_frid:
            raise ValueError("Last render FRID is not set for a non-initial module")

        next_frid, next_module = plain_module.get_next_frid(pr.last_render_frid, pr.last_render_module.module_name)
        render_range = plain_spec.get_render_range_from(next_frid, next_module.plain_source)
        return RenderChoice(
            module=next_module,
            render_range=None if force_render else render_range,
            wipe_later_modules=force_render,
            choice_type="continue_from_frid",
        )

    next_module = plain_module.get_next_module(pr.last_render_module.module_name) or pr.last_render_module
    return RenderChoice(
        module=next_module,
        render_range=None,
        choice_type="module_start",
        is_destructive=pr.last_render_module.module_name == plain_module.module_name,
    )


def get_render_choices(
    plain_module: PlainModule,
    plain_module_render_state: PlainModuleRenderState,
    force_render: bool = False,
) -> dict[str, RenderChoice]:
    render_choices = list[RenderChoice]()
    module_start_points = list[str]()

    # Decide whether the detected change actually requires a decision. A spec change that only
    # adds work *after* the last-rendered functionality (e.g. a new functionality appended to
    # the end of the last module) does not affect anything already rendered, so it is treated
    # like a normal "continue" rather than a change that requires choosing where to restart.
    partial_start = None
    treat_as_continue = plain_module_render_state.change is None
    if plain_module_render_state.change is not None and plain_module_render_state.change_type == "spec_change":
        partial_start = determine_partial_render_start(plain_module)
        if change_is_only_future_work(plain_module, plain_module_render_state, partial_start):
            treat_as_continue = True

    if treat_as_continue:
        # Continue / resume from the last-rendered position. The change-driven blocks below decide
        # the start otherwise — once a change touches already-rendered work, resuming from the old
        # position would build on stale code.
        render_choices.append(_resume_render_choice(plain_module, plain_module_render_state, force_render))

    else:
        # change is set here (otherwise treat_as_continue would be True), so change_type is always
        # "spec_change" or "code_change" and get_all_affected_modules_from_change is well-defined.
        all_affected_modules = get_all_affected_modules_from_change(plain_module, plain_module_render_state)

        if plain_module_render_state.change_type == "spec_change" and partial_start is not None:
            # Optimized partial render: render from the changed functionality, keeping the
            # module's earlier (unchanged) functionalities.
            render_range = plain_spec.get_render_range_from(partial_start.frid, partial_start.module.plain_source)
            render_choices.append(
                RenderChoice(
                    module=partial_start.module,
                    render_range=render_range,
                    choice_type="render_from_change",
                    wipe_later_modules=len(all_affected_modules) > 1,
                    is_destructive=True,
                )
            )

        # Full re-render of the affected module(s) from scratch. For a code change this is always
        # the action; for a spec change with no partial start (non-FR sections changed, or only
        # trailing FR removals) it is the only safe option, and when a partial start does exist it
        # is offered alongside it as the "rebuild this module cleanly" alternative. The "re-render
        # from first module" choice below remains the full-chain reset.
        if len(all_affected_modules) > 0 and all_affected_modules[0].module_name not in module_start_points:
            render_choices.append(
                RenderChoice(
                    module=all_affected_modules[0],
                    render_range=None,
                    choice_type="rerender_affected",
                    wipe_later_modules=True,
                    is_destructive=True,
                )
            )
            module_start_points.append(all_affected_modules[0].module_name)

        if len(plain_module.all_required_modules) > 0:
            first_module = plain_module.all_required_modules[0]
            if first_module.module_name != plain_module_render_state.last_render_module.module_name and (
                plain_module_render_state.change is not None
                and first_module.module_name != plain_module_render_state.change.module_name
                and first_module.module_name not in module_start_points
            ):
                render_choices.append(
                    RenderChoice(
                        module=first_module,
                        render_range=None,
                        choice_type="rerender_from_first",
                        wipe_later_modules=True,
                        is_destructive=True,
                    )
                )
                module_start_points.append(first_module.module_name)

    render_choices.append(RenderChoice(module=None, render_range=None, choice_type="quit"))
    return {str(idx): choice for idx, choice in enumerate(render_choices, start=1)}
