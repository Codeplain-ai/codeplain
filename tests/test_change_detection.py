"""Tests for the change detection logic.

Uses lightweight fake PlainModule-like objects (same pattern as
test_partial_rendering.py) to exercise the comparison algorithm
without filesystem or real PlainModule dependencies.
"""

from change_detection import FunctionalityChange, _detect_module_changes, determine_partial_render_start


class FakeModule:
    def __init__(
        self,
        module_name: str,
        current_frs: list[str],
        stored_frs: list[str] | None = None,
        current_non_fr_hash: str = "match",
        stored_non_fr_hash: str | None = "match",
    ):
        self.module_name = module_name
        self._current_frs = current_frs
        self._stored_frs = stored_frs
        self._current_non_fr_hash = current_non_fr_hash
        self._stored_non_fr_hash = stored_non_fr_hash
        self.required_modules: list[FakeModule] = []

    @property
    def all_required_modules(self) -> list["FakeModule"]:
        result = []
        for rm in self.required_modules:
            result.extend(rm.all_required_modules)
            result.append(rm)
        return result

    def load_module_metadata(self) -> dict | None:
        if self._stored_frs is None:
            return None
        metadata: dict = {"functionalities": self._stored_frs}
        if self._stored_non_fr_hash is not None:
            metadata["non_functional_source_hash"] = self._stored_non_fr_hash
        return metadata

    def _get_module_functional_requirements(self) -> list[str]:
        return self._current_frs

    def get_module_non_functional_source_hash(self) -> str:
        return self._current_non_fr_hash


# --- No changes ---


def test_identical_specs_no_changes():
    module = FakeModule("mod", ["A", "B", "C"], ["A", "B", "C"])
    changes = _detect_module_changes(module)
    assert changes == []


def test_empty_both_no_changes():
    module = FakeModule("mod", [], [])
    changes = _detect_module_changes(module)
    assert changes == []


# --- All added (no metadata / never rendered) ---


def test_no_metadata_all_added():
    module = FakeModule("mod", ["A", "B"], stored_frs=None)
    changes = _detect_module_changes(module)
    assert len(changes) == 2
    assert all(c.change_type == "added" for c in changes)
    assert changes[0] == FunctionalityChange(module="mod", frid="1", change_type="added")
    assert changes[1] == FunctionalityChange(module="mod", frid="2", change_type="added")


def test_empty_stored_all_added():
    module = FakeModule("mod", ["A", "B"], stored_frs=[])
    changes = _detect_module_changes(module)
    assert len(changes) == 2
    assert all(c.change_type == "added" for c in changes)


# --- All removed ---


def test_no_current_frs_all_removed():
    module = FakeModule("mod", [], stored_frs=["A", "B", "C"])
    changes = _detect_module_changes(module)
    assert len(changes) == 3
    assert all(c.change_type == "removed" for c in changes)
    assert changes[0].frid == "1"
    assert changes[1].frid == "2"
    assert changes[2].frid == "3"


# --- Edits ---


def test_single_edit():
    module = FakeModule("mod", ["A modified", "B"], stored_frs=["A", "B"])
    changes = _detect_module_changes(module)
    assert len(changes) == 1
    assert changes[0] == FunctionalityChange(module="mod", frid="1", change_type="edited")


def test_multiple_edits():
    module = FakeModule("mod", ["X", "Y", "Z"], stored_frs=["A", "B", "C"])
    changes = _detect_module_changes(module)
    assert len(changes) == 3
    assert all(c.change_type == "edited" for c in changes)


# --- Additions ---


def test_addition_at_end():
    module = FakeModule("mod", ["A", "B", "C"], stored_frs=["A", "B"])
    changes = _detect_module_changes(module)
    assert len(changes) == 1
    assert changes[0] == FunctionalityChange(module="mod", frid="3", change_type="added")


# --- Removals ---


def test_removal_at_end():
    module = FakeModule("mod", ["A", "B"], stored_frs=["A", "B", "C"])
    changes = _detect_module_changes(module)
    assert len(changes) == 1
    assert changes[0] == FunctionalityChange(module="mod", frid="3", change_type="removed")


# --- Moves ---


def test_swap_detected_as_moves():
    module = FakeModule("mod", ["B", "A"], stored_frs=["A", "B"])
    changes = _detect_module_changes(module)
    moves = [c for c in changes if c.change_type == "moved"]
    assert len(moves) == 2
    assert FunctionalityChange(module="mod", frid="1", change_type="moved", detail="2") in moves
    assert FunctionalityChange(module="mod", frid="2", change_type="moved", detail="1") in moves


def test_move_to_later_position():
    module = FakeModule("mod", ["B", "C", "A"], stored_frs=["A", "B", "C"])
    changes = _detect_module_changes(module)
    moves = [c for c in changes if c.change_type == "moved"]
    assert len(moves) == 3


# --- Combined changes ---


def test_edit_and_addition():
    module = FakeModule("mod", ["X", "B", "C"], stored_frs=["A", "B"])
    changes = _detect_module_changes(module)
    edits = [c for c in changes if c.change_type == "edited"]
    added = [c for c in changes if c.change_type == "added"]
    assert len(edits) == 1
    assert edits[0].frid == "1"
    assert len(added) == 1
    assert added[0].frid == "3"


def test_edit_and_removal():
    module = FakeModule("mod", ["X"], stored_frs=["A", "B"])
    changes = _detect_module_changes(module)
    edits = [c for c in changes if c.change_type == "edited"]
    removed = [c for c in changes if c.change_type == "removed"]
    assert len(edits) == 1
    assert edits[0].frid == "1"
    assert len(removed) == 1
    assert removed[0].frid == "2"


def test_move_and_addition():
    # Old: [A, B], New: [B, A, C] → A moved 1→2, B moved 2→1, C added
    module = FakeModule("mod", ["B", "A", "C"], stored_frs=["A", "B"])
    changes = _detect_module_changes(module)
    moves = [c for c in changes if c.change_type == "moved"]
    added = [c for c in changes if c.change_type == "added"]
    assert len(moves) == 2
    assert len(added) == 1
    assert added[0].frid == "3"


# --- Duplicate FR texts ---


def test_duplicate_texts_matched_by_position():
    module = FakeModule("mod", ["A", "A", "B"], stored_frs=["A", "A", "B"])
    changes = _detect_module_changes(module)
    assert changes == []


def test_duplicate_texts_with_edit():
    module = FakeModule("mod", ["A", "X", "B"], stored_frs=["A", "A", "B"])
    changes = _detect_module_changes(module)
    assert len(changes) == 1
    assert changes[0] == FunctionalityChange(module="mod", frid="2", change_type="edited")


# --- determine_partial_render_start ---


def test_partial_render_start_no_changes():
    module = FakeModule("mod", ["A", "B", "C"], stored_frs=["A", "B", "C"])
    result = determine_partial_render_start(module)
    assert result is None


def test_partial_render_start_edit_at_fr3():
    module = FakeModule("mod", ["A", "B", "C modified", "D", "E"], stored_frs=["A", "B", "C", "D", "E"])
    result = determine_partial_render_start(module)
    assert result is not None
    assert result.module.module_name == "mod"
    assert result.frid == "3"


def test_partial_render_start_removal_at_fr3():
    module = FakeModule("mod", ["A", "B", "D", "E"], stored_frs=["A", "B", "C", "D", "E"])
    result = determine_partial_render_start(module)
    assert result is not None
    assert result.module.module_name == "mod"
    assert result.frid == "3"


def test_partial_render_start_addition_at_end():
    module = FakeModule("mod", ["A", "B", "C", "D"], stored_frs=["A", "B", "C"])
    result = determine_partial_render_start(module)
    assert result is not None
    assert result.module.module_name == "mod"
    assert result.frid == "4"


def test_partial_render_start_swap_returns_earliest_position():
    """A swap of FRs 1 and 2 should start partial render from FRID 1."""
    module = FakeModule("mod", ["B", "A", "C"], stored_frs=["A", "B", "C"])
    result = determine_partial_render_start(module)
    assert result is not None
    assert result.frid == "1"


def test_partial_render_start_mid_module_swap_returns_earliest_position():
    """A swap of FRs 3 and 4 in the middle of a module should start from FRID 3."""
    module = FakeModule("mod", ["A", "B", "D", "C", "E"], stored_frs=["A", "B", "C", "D", "E"])
    result = determine_partial_render_start(module)
    assert result is not None
    assert result.frid == "3"


def test_partial_render_start_cyclic_move_returns_earliest_position():
    """A 3-cycle move should start from the lowest position in the cycle."""
    module = FakeModule("mod", ["B", "C", "A"], stored_frs=["A", "B", "C"])
    result = determine_partial_render_start(module)
    assert result is not None
    assert result.frid == "1"


def test_partial_render_start_multi_module_change_in_required():
    req_module = FakeModule("req", ["R1", "R2 modified", "R3"], stored_frs=["R1", "R2", "R3"])
    top_module = FakeModule("top", ["T1", "T2"], stored_frs=["T1", "T2"])
    top_module.required_modules = [req_module]

    result = determine_partial_render_start(top_module)
    assert result is not None
    assert result.module.module_name == "req"
    assert result.frid == "2"


def test_partial_render_start_multi_module_no_change_in_required():
    req_module = FakeModule("req", ["R1", "R2"], stored_frs=["R1", "R2"])
    top_module = FakeModule("top", ["T1 modified", "T2"], stored_frs=["T1", "T2"])
    top_module.required_modules = [req_module]

    result = determine_partial_render_start(top_module)
    assert result is not None
    assert result.module.module_name == "top"
    assert result.frid == "1"


def test_partial_render_start_multi_module_no_changes_returns_none():
    req_module = FakeModule("req", ["R1", "R2"], stored_frs=["R1", "R2"])
    top_module = FakeModule("top", ["T1"], stored_frs=["T1"])
    top_module.required_modules = [req_module]

    result = determine_partial_render_start(top_module)
    assert result is None


def test_partial_render_start_changes_in_multiple_modules_returns_earliest():
    """When both a required module and the top module changed, the earliest module in the
    chain (the required one) determines the start point."""
    req_module = FakeModule("req", ["R1", "R2"], stored_frs=["R1"])  # R2 added
    top_module = FakeModule("top", ["T1 modified"], stored_frs=["T1"])  # T1 edited
    top_module.required_modules = [req_module]

    result = determine_partial_render_start(top_module)
    assert result is not None
    assert result.module.module_name == "req"
    assert result.frid == "2"


def test_partial_render_start_deep_chain_change_in_middle():
    mod_a = FakeModule("a", ["A1"], stored_frs=["A1"])
    mod_b = FakeModule("b", ["B1", "B2"], stored_frs=["B1"])
    mod_b.required_modules = [mod_a]
    mod_c = FakeModule("c", ["C1"], stored_frs=["C1"])
    mod_c.required_modules = [mod_b]

    result = determine_partial_render_start(mod_c)
    assert result is not None
    assert result.module.module_name == "b"
    assert result.frid == "2"


def test_partial_render_start_never_rendered():
    module = FakeModule("mod", ["A", "B"], stored_frs=None)
    result = determine_partial_render_start(module)
    assert result is not None
    assert result.frid == "1"


def test_partial_render_start_trailing_removal_returns_none():
    """Removing the last FR doesn't require rendering any remaining FRs."""
    module = FakeModule("mod", ["A", "B"], stored_frs=["A", "B", "C"])
    result = determine_partial_render_start(module)
    assert result is None


def test_partial_render_start_trailing_removal_with_earlier_edit():
    """Trailing removal combined with an earlier edit should still detect the edit."""
    module = FakeModule("mod", ["A modified", "B"], stored_frs=["A", "B", "C"])
    result = determine_partial_render_start(module)
    assert result is not None
    assert result.frid == "1"


# --- Non-functional content changes ---


def test_partial_render_start_blocked_by_non_fr_change():
    """An FR edit alongside a non-FR change (e.g. new definition) must block partial render."""
    module = FakeModule(
        "mod",
        ["A modified", "B"],
        stored_frs=["A", "B"],
        current_non_fr_hash="v2",
        stored_non_fr_hash="v1",
    )
    result = determine_partial_render_start(module)
    assert result is None


def test_partial_render_start_non_fr_only_change_returns_none():
    """A non-FR-only change (FRs identical) blocks partial render."""
    module = FakeModule(
        "mod",
        ["A", "B"],
        stored_frs=["A", "B"],
        current_non_fr_hash="v2",
        stored_non_fr_hash="v1",
    )
    result = determine_partial_render_start(module)
    assert result is None


def test_partial_render_start_missing_stored_non_fr_hash_blocks():
    """Older metadata without the non-FR hash must be treated as 'changed' (safe default)."""
    module = FakeModule(
        "mod",
        ["A modified", "B"],
        stored_frs=["A", "B"],
        stored_non_fr_hash=None,
    )
    result = determine_partial_render_start(module)
    assert result is None


def test_partial_render_start_non_fr_change_in_required_module_blocks():
    req_module = FakeModule(
        "req",
        ["R1 modified"],
        stored_frs=["R1"],
        current_non_fr_hash="v2",
        stored_non_fr_hash="v1",
    )
    top_module = FakeModule("top", ["T1"], stored_frs=["T1"])
    top_module.required_modules = [req_module]

    result = determine_partial_render_start(top_module)
    assert result is None
