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

        current_fr_count = len(module._get_module_functional_requirements())
        earliest_frid = _get_earliest_affected_frid(changes, current_fr_count)
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


def _get_earliest_affected_frid(changes: list[FunctionalityChange], current_fr_count: int) -> str | None:
    """Earliest FRID (in current spec numbering) that must be re-rendered.

    Returns the minimum position across all changes:
    - added / edited: the FRID itself.
    - removed: the position the removal opened up (now occupied by the next FR).
    - moved: the FR's old position.

    Correctness does not rely on any single change type's position being individually
    "the" earliest (in particular, a move's old position is *not* always its earliest
    touched position once moves mix with adds/removes). The guarantee is structural:
    the first index at which the new spec diverges from the old is always emitted as
    some change anchored at that index — a move with that old position, or an
    edit/removal/addition there — so the minimum over all change FRIDs lands at or
    before the true first divergence. Rendering runs from this FRID to the end of the
    module, so an at-or-before start point is always safe (it can re-render unchanged
    trailing FRs, but never skips a changed one).

    Returns None when every change is a removal beyond the current spec length
    (only trailing FRs were removed, so nothing needs rendering).
    """
    earliest = None
    for change in changes:
        frid_int = int(change.frid)
        if change.change_type == "removed" and frid_int > current_fr_count:
            continue
        if earliest is None or frid_int < earliest:
            earliest = frid_int
    if earliest is None:
        return None
    return str(earliest)


def _detect_module_changes(module: "PlainModule") -> list[FunctionalityChange]:
    metadata = module.load_module_metadata()
    old_frs: list[str] = metadata.get(MODULE_FUNCTIONALITIES_KEY, []) if metadata else []
    new_frs: list[str] = module._get_module_functional_requirements()

    if old_frs == new_frs:
        return []

    moves, edits, removed, added = _classify_changes(old_frs, new_frs)

    changes: list[FunctionalityChange] = []
    name = module.module_name

    for old_idx, new_idx in moves:
        old_frid = _frid_from_index(old_idx)
        new_frid = _frid_from_index(new_idx)
        changes.append(FunctionalityChange(module=name, frid=old_frid, change_type="moved", detail=new_frid))

    for idx in edits:
        changes.append(FunctionalityChange(module=name, frid=_frid_from_index(idx), change_type="edited"))

    for idx in removed:
        changes.append(FunctionalityChange(module=name, frid=_frid_from_index(idx), change_type="removed"))

    for idx in added:
        changes.append(FunctionalityChange(module=name, frid=_frid_from_index(idx), change_type="added"))

    return changes


def _classify_changes(
    old_frs: list[str], new_frs: list[str]
) -> tuple[list[tuple[int, int]], list[int], list[int], list[int]]:
    matched_old: set[int] = set()
    matched_new: set[int] = set()

    for i in range(min(len(old_frs), len(new_frs))):
        if old_frs[i] == new_frs[i]:
            matched_old.add(i)
            matched_new.add(i)

    content_matches: list[tuple[int, int]] = []
    for old_idx in range(len(old_frs)):
        if old_idx in matched_old:
            continue
        for new_idx in range(len(new_frs)):
            if new_idx in matched_new:
                continue
            if old_frs[old_idx] == new_frs[new_idx]:
                content_matches.append((old_idx, new_idx))
                matched_old.add(old_idx)
                matched_new.add(new_idx)
                break

    moves: list[tuple[int, int]] = []
    if content_matches and _has_relative_order_change(content_matches):
        moves = content_matches

    edits: list[int] = []
    for i in range(min(len(old_frs), len(new_frs))):
        if i not in matched_old and i not in matched_new:
            edits.append(i)
            matched_old.add(i)
            matched_new.add(i)

    removed = [i for i in range(len(old_frs)) if i not in matched_old]
    added = [i for i in range(len(new_frs)) if i not in matched_new]

    return moves, edits, removed, added


def _has_relative_order_change(matches: list[tuple[int, int]]) -> bool:
    """Check if content matches represent a true reorder (relative order changed).

    If all matches preserve relative order (sorted by old_idx gives same ordering
    as sorted by new_idx), it's just a positional shift from insertions/removals.
    """
    if len(matches) <= 1:
        return False
    sorted_by_old = sorted(matches, key=lambda m: m[0])
    new_indices = [m[1] for m in sorted_by_old]
    for i in range(len(new_indices) - 1):
        if new_indices[i] > new_indices[i + 1]:
            return True
    return False


def _frid_from_index(index: int) -> str:
    return str(index + 1)
