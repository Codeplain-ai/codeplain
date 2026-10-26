from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from plain_modules import PlainModule

MODULE_FUNCTIONALITIES_KEY = "functionalities"
NON_FUNCTIONAL_SOURCE_HASH_KEY = "non_functional_source_hash"


@dataclass
class FunctionalityChange:
    module: str
    frid: str
    change_type: Literal["added", "removed", "edited", "moved"]
    detail: str | None = None


@dataclass
class PartialRenderStart:
    module: "PlainModule"
    frid: str


def determine_partial_render_start(plain_module: "PlainModule") -> PartialRenderStart | None:
    """Determine where to start partial rendering based on spec changes.

    Returns None (only full render is safe) if non-FR sections changed
    (e.g. definitions, implementation reqs) since previously-rendered FRs
    were generated without that context, if no changes are found, or if
    all changes are trailing removals that don't require rendering.
    """
    all_modules = plain_module.all_required_modules + [plain_module]

    for module in all_modules:
        if _non_functional_content_changed(module):
            return None

        changes = _detect_module_changes(module)
        if not changes:
            continue

        earliest_frid = _get_earliest_affected_frid(changes, _stored_frids(module), _current_frids(module))
        if earliest_frid is None:
            continue
        return PartialRenderStart(module=module, frid=earliest_frid)

    return None


def _non_functional_content_changed(module: "PlainModule") -> bool:
    """Check whether anything outside functional specs changed since last render.

    A missing stored hash (older builds) is treated as changed — partial rendering
    is unsafe without a known baseline.
    """
    metadata = module.load_module_metadata()
    if not metadata:
        return False
    stored_hash = metadata.get(NON_FUNCTIONAL_SOURCE_HASH_KEY)
    if stored_hash is None:
        return True
    return stored_hash != module.get_module_non_functional_source_hash()


def _stored_functionalities(module: "PlainModule") -> list[dict[str, str]]:
    metadata = module.load_module_metadata()
    return metadata.get(MODULE_FUNCTIONALITIES_KEY, []) if metadata else []


def _stored_frids(module: "PlainModule") -> list[str]:
    return [functionality["id"] for functionality in _stored_functionalities(module)]


def _current_frids(module: "PlainModule") -> list[str]:
    return [functionality["id"] for functionality in module._get_module_functional_requirements()]


def _get_earliest_affected_frid(
    changes: list[FunctionalityChange], old_frids: list[str], new_frids: list[str]
) -> str | None:
    """The func-id of the earliest functionality in the new order that must be re-rendered.

    Rendering runs from this functionality to the end of the module, so an at-or-before start
    point is always safe: it can re-render unchanged trailing functionalities, but never skips
    a changed one.

    Returns None when every change is a removal past the end of the new order (only trailing
    functionalities were removed, so nothing needs rendering).
    """
    earliest = None
    for change in changes:
        position = _affected_position(change, old_frids, new_frids)
        if position is None:
            continue
        if earliest is None or position < earliest:
            earliest = position
    if earliest is None:
        return None
    return new_frids[earliest]


def _affected_position(change: FunctionalityChange, old_frids: list[str], new_frids: list[str]) -> int | None:
    """Where in the new order the change must be rendered from.

    A removed functionality has no place in the new order, so the one that took its place is
    rendered instead. A removal past the end of the new order needs no render.
    """
    if change.change_type == "removed":
        position = old_frids.index(change.frid)
        return position if position < len(new_frids) else None

    return new_frids.index(change.frid)


def _detect_module_changes(module: "PlainModule") -> list[FunctionalityChange]:
    old_frs = _stored_functionalities(module)
    new_frs = module._get_module_functional_requirements()

    if old_frs == new_frs:
        return []

    old_frids = [functionality["id"] for functionality in old_frs]
    new_frids = [functionality["id"] for functionality in new_frs]
    old_markdown = {functionality["id"]: functionality["markdown"] for functionality in old_frs}

    changes: list[FunctionalityChange] = []
    name = module.module_name

    for frid in _moved_frids(old_frids, new_frids):
        changes.append(FunctionalityChange(module=name, frid=frid, change_type="moved"))

    for functionality in new_frs:
        frid = functionality["id"]
        if frid in old_markdown and functionality["markdown"] != old_markdown[frid]:
            changes.append(FunctionalityChange(module=name, frid=frid, change_type="edited"))

    for frid in old_frids:
        if frid not in new_frids:
            changes.append(FunctionalityChange(module=name, frid=frid, change_type="removed"))

    for frid in new_frids:
        if frid not in old_markdown:
            changes.append(FunctionalityChange(module=name, frid=frid, change_type="added"))

    return changes


def _moved_frids(old_frids: list[str], new_frids: list[str]) -> list[str]:
    """The func-ids the two orders keep but place differently, in the new order.

    Only the functionalities present in both orders are compared, so an addition or a removal
    alone shifts no one: it is a move only when the kept functionalities swap places.
    """
    kept = set(old_frids) & set(new_frids)
    old_order = [frid for frid in old_frids if frid in kept]
    new_order = [frid for frid in new_frids if frid in kept]

    return [frid for position, frid in enumerate(new_order) if old_order[position] != frid]
