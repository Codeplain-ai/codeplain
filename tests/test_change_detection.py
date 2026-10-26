"""Tests for the change detection logic.

Uses lightweight fake PlainModule-like objects (same pattern as
test_partial_rendering.py) to exercise the comparison algorithm
without filesystem or real PlainModule dependencies.
"""

from change_detection import FunctionalityChange, _detect_module_changes, determine_partial_render_start


def _frs(*items) -> list[dict[str, str]]:
    """Functionalities from a func-id, or from a `(func-id, text)` pair when the text matters."""
    functionalities = []
    for item in items:
        frid, markdown = item if isinstance(item, tuple) else (item, f"The {item} functionality.")
        functionalities.append({"id": frid, "markdown": markdown})
    return functionalities


class FakeModule:
    def __init__(
        self,
        module_name: str,
        current_frs: list[dict[str, str]],
        stored_frs: list[dict[str, str]] | None = None,
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

    def _get_module_functional_requirements(self) -> list[dict[str, str]]:
        return self._current_frs

    def get_module_non_functional_source_hash(self) -> str:
        return self._current_non_fr_hash


# --- No changes ---


def test_identical_specs_no_changes():
    module = FakeModule("mod", _frs("fr-a", "fr-b", "fr-c"), _frs("fr-a", "fr-b", "fr-c"))
    changes = _detect_module_changes(module)
    assert changes == []


def test_empty_both_no_changes():
    module = FakeModule("mod", [], [])
    changes = _detect_module_changes(module)
    assert changes == []


# --- All added (no metadata / never rendered) ---


def test_no_metadata_all_added():
    module = FakeModule("mod", _frs("fr-a", "fr-b"), stored_frs=None)
    changes = _detect_module_changes(module)
    assert changes == [
        FunctionalityChange(module="mod", frid="fr-a", change_type="added"),
        FunctionalityChange(module="mod", frid="fr-b", change_type="added"),
    ]


def test_empty_stored_all_added():
    module = FakeModule("mod", _frs("fr-a", "fr-b"), stored_frs=[])
    changes = _detect_module_changes(module)
    assert len(changes) == 2
    assert all(c.change_type == "added" for c in changes)


# --- All removed ---


def test_no_current_frs_all_removed():
    module = FakeModule("mod", [], stored_frs=_frs("fr-a", "fr-b", "fr-c"))
    changes = _detect_module_changes(module)
    assert all(c.change_type == "removed" for c in changes)
    assert [c.frid for c in changes] == ["fr-a", "fr-b", "fr-c"]


# --- Edits ---


def test_single_edit():
    module = FakeModule("mod", _frs(("fr-a", "new text"), "fr-b"), stored_frs=_frs("fr-a", "fr-b"))
    changes = _detect_module_changes(module)
    assert changes == [FunctionalityChange(module="mod", frid="fr-a", change_type="edited")]


def test_multiple_edits():
    module = FakeModule(
        "mod",
        _frs(("fr-a", "new a"), ("fr-b", "new b"), ("fr-c", "new c")),
        stored_frs=_frs("fr-a", "fr-b", "fr-c"),
    )
    changes = _detect_module_changes(module)
    assert len(changes) == 3
    assert all(c.change_type == "edited" for c in changes)


def test_two_functionalities_with_the_same_text_are_told_apart_by_id():
    same_text = "The same text."
    module = FakeModule(
        "mod",
        _frs(("fr-a", same_text), ("fr-b", "new b")),
        stored_frs=_frs(("fr-a", same_text), ("fr-b", same_text)),
    )
    changes = _detect_module_changes(module)
    assert changes == [FunctionalityChange(module="mod", frid="fr-b", change_type="edited")]


# --- Additions ---


def test_addition_at_end():
    module = FakeModule("mod", _frs("fr-a", "fr-b", "fr-c"), stored_frs=_frs("fr-a", "fr-b"))
    changes = _detect_module_changes(module)
    assert changes == [FunctionalityChange(module="mod", frid="fr-c", change_type="added")]


def test_addition_in_the_middle_moves_nobody():
    module = FakeModule("mod", _frs("fr-a", "fr-x", "fr-b"), stored_frs=_frs("fr-a", "fr-b"))
    changes = _detect_module_changes(module)
    assert changes == [FunctionalityChange(module="mod", frid="fr-x", change_type="added")]


# --- Removals ---


def test_removal_at_end():
    module = FakeModule("mod", _frs("fr-a", "fr-b"), stored_frs=_frs("fr-a", "fr-b", "fr-c"))
    changes = _detect_module_changes(module)
    assert changes == [FunctionalityChange(module="mod", frid="fr-c", change_type="removed")]


def test_removal_in_the_middle_moves_nobody():
    module = FakeModule("mod", _frs("fr-a", "fr-c"), stored_frs=_frs("fr-a", "fr-b", "fr-c"))
    changes = _detect_module_changes(module)
    assert changes == [FunctionalityChange(module="mod", frid="fr-b", change_type="removed")]


# --- Moves ---


def test_swap_detected_as_moves():
    module = FakeModule("mod", _frs("fr-b", "fr-a"), stored_frs=_frs("fr-a", "fr-b"))
    changes = _detect_module_changes(module)
    moves = [c for c in changes if c.change_type == "moved"]
    assert [c.frid for c in moves] == ["fr-b", "fr-a"]


def test_move_to_later_position():
    module = FakeModule("mod", _frs("fr-b", "fr-c", "fr-a"), stored_frs=_frs("fr-a", "fr-b", "fr-c"))
    changes = _detect_module_changes(module)
    moves = [c for c in changes if c.change_type == "moved"]
    assert len(moves) == 3


# --- Combined changes ---


def test_edit_and_addition():
    module = FakeModule(
        "mod",
        _frs(("fr-a", "new a"), "fr-b", "fr-c"),
        stored_frs=_frs("fr-a", "fr-b"),
    )
    changes = _detect_module_changes(module)
    edits = [c for c in changes if c.change_type == "edited"]
    added = [c for c in changes if c.change_type == "added"]
    assert [c.frid for c in edits] == ["fr-a"]
    assert [c.frid for c in added] == ["fr-c"]


def test_edit_and_removal():
    module = FakeModule("mod", _frs(("fr-a", "new a")), stored_frs=_frs("fr-a", "fr-b"))
    changes = _detect_module_changes(module)
    edits = [c for c in changes if c.change_type == "edited"]
    removed = [c for c in changes if c.change_type == "removed"]
    assert [c.frid for c in edits] == ["fr-a"]
    assert [c.frid for c in removed] == ["fr-b"]


def test_move_and_addition():
    # Old: [a, b], New: [b, a, c] -> a and b swapped, c added
    module = FakeModule("mod", _frs("fr-b", "fr-a", "fr-c"), stored_frs=_frs("fr-a", "fr-b"))
    changes = _detect_module_changes(module)
    moves = [c for c in changes if c.change_type == "moved"]
    added = [c for c in changes if c.change_type == "added"]
    assert len(moves) == 2
    assert [c.frid for c in added] == ["fr-c"]


# --- determine_partial_render_start ---


def test_partial_render_start_no_changes():
    module = FakeModule("mod", _frs("fr-a", "fr-b", "fr-c"), _frs("fr-a", "fr-b", "fr-c"))
    assert determine_partial_render_start(module) is None


def test_partial_render_start_edit():
    module = FakeModule(
        "mod",
        _frs("fr-a", "fr-b", ("fr-c", "new c"), "fr-d", "fr-e"),
        stored_frs=_frs("fr-a", "fr-b", "fr-c", "fr-d", "fr-e"),
    )
    result = determine_partial_render_start(module)
    assert result is not None
    assert result.module.module_name == "mod"
    assert result.frid == "fr-c"


def test_partial_render_start_removal_starts_at_what_took_its_place():
    module = FakeModule(
        "mod",
        _frs("fr-a", "fr-b", "fr-d", "fr-e"),
        stored_frs=_frs("fr-a", "fr-b", "fr-c", "fr-d", "fr-e"),
    )
    result = determine_partial_render_start(module)
    assert result is not None
    assert result.frid == "fr-d"


def test_partial_render_start_addition_at_end():
    module = FakeModule("mod", _frs("fr-a", "fr-b", "fr-c", "fr-d"), stored_frs=_frs("fr-a", "fr-b", "fr-c"))
    result = determine_partial_render_start(module)
    assert result is not None
    assert result.frid == "fr-d"


def test_partial_render_start_swap_returns_earliest_position():
    module = FakeModule("mod", _frs("fr-b", "fr-a", "fr-c"), stored_frs=_frs("fr-a", "fr-b", "fr-c"))
    result = determine_partial_render_start(module)
    assert result is not None
    assert result.frid == "fr-b"


def test_partial_render_start_mid_module_swap_returns_earliest_position():
    module = FakeModule(
        "mod",
        _frs("fr-a", "fr-b", "fr-d", "fr-c", "fr-e"),
        stored_frs=_frs("fr-a", "fr-b", "fr-c", "fr-d", "fr-e"),
    )
    result = determine_partial_render_start(module)
    assert result is not None
    assert result.frid == "fr-d"


def test_partial_render_start_cyclic_move_returns_earliest_position():
    module = FakeModule("mod", _frs("fr-b", "fr-c", "fr-a"), stored_frs=_frs("fr-a", "fr-b", "fr-c"))
    result = determine_partial_render_start(module)
    assert result is not None
    assert result.frid == "fr-b"


def test_partial_render_start_multi_module_change_in_required():
    req_module = FakeModule(
        "req", _frs("req-a", ("req-b", "new b"), "req-c"), stored_frs=_frs("req-a", "req-b", "req-c")
    )
    top_module = FakeModule("top", _frs("top-a", "top-b"), stored_frs=_frs("top-a", "top-b"))
    top_module.required_modules = [req_module]

    result = determine_partial_render_start(top_module)
    assert result is not None
    assert result.module.module_name == "req"
    assert result.frid == "req-b"


def test_partial_render_start_multi_module_no_change_in_required():
    req_module = FakeModule("req", _frs("req-a", "req-b"), stored_frs=_frs("req-a", "req-b"))
    top_module = FakeModule("top", _frs(("top-a", "new a"), "top-b"), stored_frs=_frs("top-a", "top-b"))
    top_module.required_modules = [req_module]

    result = determine_partial_render_start(top_module)
    assert result is not None
    assert result.module.module_name == "top"
    assert result.frid == "top-a"


def test_partial_render_start_multi_module_no_changes_returns_none():
    req_module = FakeModule("req", _frs("req-a", "req-b"), stored_frs=_frs("req-a", "req-b"))
    top_module = FakeModule("top", _frs("top-a"), stored_frs=_frs("top-a"))
    top_module.required_modules = [req_module]

    assert determine_partial_render_start(top_module) is None


def test_partial_render_start_changes_in_multiple_modules_returns_earliest():
    """When both a required module and the top module changed, the earliest module in the
    chain (the required one) determines the start point."""
    req_module = FakeModule("req", _frs("req-a", "req-b"), stored_frs=_frs("req-a"))
    top_module = FakeModule("top", _frs(("top-a", "new a")), stored_frs=_frs("top-a"))
    top_module.required_modules = [req_module]

    result = determine_partial_render_start(top_module)
    assert result is not None
    assert result.module.module_name == "req"
    assert result.frid == "req-b"


def test_partial_render_start_deep_chain_change_in_middle():
    mod_a = FakeModule("a", _frs("a-one"), stored_frs=_frs("a-one"))
    mod_b = FakeModule("b", _frs("b-one", "b-two"), stored_frs=_frs("b-one"))
    mod_b.required_modules = [mod_a]
    mod_c = FakeModule("c", _frs("c-one"), stored_frs=_frs("c-one"))
    mod_c.required_modules = [mod_b]

    result = determine_partial_render_start(mod_c)
    assert result is not None
    assert result.module.module_name == "b"
    assert result.frid == "b-two"


def test_partial_render_start_never_rendered():
    module = FakeModule("mod", _frs("fr-a", "fr-b"), stored_frs=None)
    result = determine_partial_render_start(module)
    assert result is not None
    assert result.frid == "fr-a"


def test_partial_render_start_trailing_removal_returns_none():
    """Removing the last functionality doesn't require rendering any remaining one."""
    module = FakeModule("mod", _frs("fr-a", "fr-b"), stored_frs=_frs("fr-a", "fr-b", "fr-c"))
    assert determine_partial_render_start(module) is None


def test_partial_render_start_trailing_removal_with_earlier_edit():
    module = FakeModule("mod", _frs(("fr-a", "new a"), "fr-b"), stored_frs=_frs("fr-a", "fr-b", "fr-c"))
    result = determine_partial_render_start(module)
    assert result is not None
    assert result.frid == "fr-a"


# --- Non-functional content changes ---


def test_partial_render_start_blocked_by_non_fr_change():
    """An edit alongside a non-FR change (e.g. new definition) must block partial render."""
    module = FakeModule(
        "mod",
        _frs(("fr-a", "new a"), "fr-b"),
        stored_frs=_frs("fr-a", "fr-b"),
        current_non_fr_hash="v2",
        stored_non_fr_hash="v1",
    )
    assert determine_partial_render_start(module) is None


def test_partial_render_start_non_fr_only_change_returns_none():
    module = FakeModule(
        "mod",
        _frs("fr-a", "fr-b"),
        stored_frs=_frs("fr-a", "fr-b"),
        current_non_fr_hash="v2",
        stored_non_fr_hash="v1",
    )
    assert determine_partial_render_start(module) is None


def test_partial_render_start_missing_stored_non_fr_hash_blocks():
    """Older metadata without the non-FR hash must be treated as 'changed' (safe default)."""
    module = FakeModule(
        "mod",
        _frs(("fr-a", "new a"), "fr-b"),
        stored_frs=_frs("fr-a", "fr-b"),
        stored_non_fr_hash=None,
    )
    assert determine_partial_render_start(module) is None


def test_partial_render_start_non_fr_change_in_required_module_blocks():
    req_module = FakeModule(
        "req",
        _frs(("req-a", "new a")),
        stored_frs=_frs("req-a"),
        current_non_fr_hash="v2",
        stored_non_fr_hash="v1",
    )
    top_module = FakeModule("top", _frs("top-a"), stored_frs=_frs("top-a"))
    top_module.required_modules = [req_module]

    assert determine_partial_render_start(top_module) is None
