from dataclasses import dataclass

from plain2code_exceptions import ModuleDoesNotExistError
from plain_modules import PlainModule


@dataclass
class PartialRender:
    module: PlainModule
    frid: str | None = None
    spec_change: bool = False
    code_change: bool = False


@dataclass
class PartialRenderChoice:
    module: PlainModule | None = None
    render_range: list[str] | None = None
    msg: str | None = None


def spec_change(plain_module: PlainModule) -> PlainModule | None:
    for required_module in plain_module.all_required_modules:
        module_metadata = required_module.load_module_metadata()
        if (
            module_metadata
            and "source_hash" in module_metadata
            and module_metadata["source_hash"] != required_module.get_module_source_hash()
        ):
            return required_module

    module_metadata = plain_module.load_module_metadata()
    if (
        module_metadata
        and "source_hash" in module_metadata
        and module_metadata["source_hash"] != plain_module.get_module_source_hash()
    ):
        return plain_module

    return None


def code_change(plain_module: PlainModule) -> PlainModule | None:
    for required_module in plain_module.all_required_modules:
        if len(required_module.required_modules) == 0:
            continue

        module_metadata = required_module.load_module_metadata()
        previous_module = required_module.required_modules[-1]
        if (
            module_metadata
            and "required_modules_code_hash" in module_metadata
            and module_metadata["required_modules_code_hash"] != previous_module.get_module_code_hash()
        ):
            return required_module

    module_metadata = plain_module.load_module_metadata()
    previous_module = plain_module.required_modules[-1]
    if (
        module_metadata
        and "required_modules_code_hash" in module_metadata
        and module_metadata["required_modules_code_hash"] != previous_module.get_module_code_hash()
    ):
        return plain_module

    return None


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

    if module is None:
        raise ModuleDoesNotExistError(
            f"Last rendered module {last_rendered_module} not found in {all_required_modules}"
        )

    pr = PartialRender(
        module=module,
        frid=last_rendered_frid,
        spec_change=False,
        code_change=False,
    )

    if sc is None and cc is None:
        return pr

    if sc is not None and module_comes_before(all_required_modules, sc, pr.module):
        pr.module = sc
        pr.spec_change = True
        pr.code_change = False
        pr.frid = None

    if cc is not None and module_comes_before(all_required_modules, cc, pr.module):
        pr.module = cc
        pr.code_change = True
        pr.spec_change = False
        pr.frid = None

    return pr
