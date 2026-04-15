from dataclasses import dataclass

from plain_modules import PlainModule


@dataclass
class PartialRender:
    module: PlainModule | None = None
    frid: str | None = None
    spec_change: bool = False
    code_change: bool = False


def spec_change(plain_module: PlainModule) -> bool:
    if len(plain_module.required_modules) == 0:
        return plain_module if plain_module.has_plain_spec_changed() else None

    for required_module in plain_module.required_modules:
        sc = spec_change(required_module)
        if sc is not None:
            return sc

    return plain_module if plain_module.has_plain_spec_changed() else None


def code_change(plain_module: PlainModule) -> bool:
    if len(plain_module.required_modules) == 0:
        return plain_module if plain_module.has_required_modules_code_changed() else None

    for required_module in plain_module.required_modules:
        cc = code_change(required_module)
        if cc is not None:
            return cc

    return plain_module if plain_module.has_required_modules_code_changed() else None


def module_comes_before(
    all_required_modules: list[PlainModule],
    module1: PlainModule,
    module2: PlainModule,
) -> bool:
    for module in all_required_modules:
        if module.module_name == module1.module_name:
            return True
        if module.module_name == module2.module_name:
            return False

    raise Exception(f"Module {module1.module_name} and {module2.module_name} not found in {all_required_modules}")


def detect_partial_rendering(plain_module: PlainModule) -> PartialRender | None:
    sc = spec_change(plain_module)
    cc = code_change(plain_module)
    all_required_modules = plain_module.all_required_modules
    last_rendered_module, last_rendered_frid = plain_module.get_last_rendered_frid()
    if last_rendered_module is None and last_rendered_frid is None:
        return None

    module = None
    for required_module in all_required_modules:
        if required_module.module_name == last_rendered_module:
            module = required_module

    pr = PartialRender(
        module=module,
        frid=last_rendered_frid,
        spec_change=False,
        code_change=False,
    )

    if sc is None and cc is None:
        return pr

    if sc is not None:
        if module_comes_before(all_required_modules, sc, pr.module):
            pr.module = sc
            pr.spec_change = True
            pr.frid = None

    if cc is not None:
        if module_comes_before(all_required_modules, cc, pr.module):
            pr.module = cc
            pr.code_change = True
            pr.spec_change = False
            pr.frid = None

    return pr
