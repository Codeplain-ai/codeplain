"""Tests for the partial rendering logic.

These tests use lightweight fake ``PlainModule``-like objects to exercise the
pure logic in ``partial_rendering`` without depending on the filesystem or a
real ``PlainModule``. The ``FakeModule`` exposes the small surface the module
under test relies on: ``module_name``, ``required_modules``,
``all_required_modules``, ``load_module_metadata``, ``get_module_source_hash``,
``get_module_code_hash`` and (for ``detect_partial_rendering``)
``get_module_render_status``.
"""

import pytest

import partial_rendering
from change_detection import PartialRenderStart
from partial_rendering import (
    PlainModuleRenderState,
    change_is_only_future_work,
    code_change,
    get_plain_module_render_state,
    get_render_choices,
    module_comes_before_or_equal,
    spec_change,
)
from plain2code_exceptions import ModuleDoesNotExistError


class FakeModule:
    def __init__(
        self,
        module_name: str,
        required_modules=None,
        metadata=None,
        source_hash: str | None = None,
        code_hash: str | None = None,
        last_rendered=(None, None),
        no_rendered_functionality=False,
        fully_rendered=False,
        next_frid=None,
        plain_source="",
    ):
        self.module_name = module_name
        self.required_modules = list(required_modules or [])
        self._metadata = metadata
        self._source_hash = source_hash if source_hash is not None else f"src-{module_name}"
        self._code_hash = code_hash if code_hash is not None else f"code-{module_name}"
        self._last_rendered = last_rendered
        self._no_rendered_functionality = no_rendered_functionality
        self._fully_rendered = fully_rendered
        self._next_frid = next_frid
        self.plain_source = plain_source

    @property
    def all_required_modules(self):
        result = []
        for rm in self.required_modules:
            if rm.required_modules:
                result.extend(rm.all_required_modules)
            result.append(rm)
        return result

    def load_module_metadata(self):
        return self._metadata

    def get_module_source_hash(self):
        return self._source_hash

    def get_module_code_hash(self):
        return self._code_hash

    def get_module_render_status(self):
        return self._last_rendered

    def has_no_rendered_functionality(self):
        return self._no_rendered_functionality

    def is_module_fully_rendered(self):
        return self._fully_rendered

    def get_next_frid(self, frid, module_name):
        return self._next_frid

    def get_next_module(self, module_name):
        all_modules = self.all_required_modules + [self]
        for idx, module in enumerate(all_modules):
            if module.module_name == module_name and idx < len(all_modules) - 1:
                return all_modules[idx + 1]
        if module_name == self.module_name:
            return None
        raise ModuleDoesNotExistError(f"Module {module_name} does not exist")


def _unchanged_metadata(module: FakeModule) -> dict:
    """Return a metadata dict that reflects the module's current hashes
    (i.e. the module has not changed since it was last rendered)."""
    metadata = {"source_hash": module.get_module_source_hash()}
    if module.required_modules:
        metadata["required_modules_code_hash"] = module.required_modules[-1].get_module_code_hash()
    return metadata


# -------------------------
# module_comes_before
# -------------------------


def test_module_comes_before_first_module_wins():
    a = FakeModule("a")
    b = FakeModule("b")
    c = FakeModule("c")

    assert module_comes_before_or_equal([a, b, c], a, b) is True
    assert module_comes_before_or_equal([a, b, c], b, a) is False
    assert module_comes_before_or_equal([a, b, c], b, c) is True


def test_module_comes_before_raises_when_neither_found():
    a = FakeModule("a")
    b = FakeModule("b")
    missing1 = FakeModule("missing1")
    missing2 = FakeModule("missing2")

    with pytest.raises(Exception, match="not found"):
        module_comes_before_or_equal([a, b], missing1, missing2)


# -------------------------
# spec_change
# -------------------------


def test_spec_change_no_metadata_anywhere_returns_none():
    """If no module has metadata yet, there is no detectable change."""
    leaf = FakeModule("leaf")
    middle = FakeModule("middle", required_modules=[leaf])
    root = FakeModule("root", required_modules=[middle])

    assert spec_change(root) is None


def test_spec_change_returns_none_when_hashes_match():
    leaf = FakeModule("leaf")
    middle = FakeModule("middle", required_modules=[leaf])
    root = FakeModule("root", required_modules=[middle])

    leaf._metadata = _unchanged_metadata(leaf)
    middle._metadata = _unchanged_metadata(middle)
    root._metadata = _unchanged_metadata(root)

    assert spec_change(root) is None


def test_spec_change_returns_top_module_when_only_root_changed():
    leaf = FakeModule("leaf")
    middle = FakeModule("middle", required_modules=[leaf])
    root = FakeModule("root", required_modules=[middle])

    leaf._metadata = _unchanged_metadata(leaf)
    middle._metadata = _unchanged_metadata(middle)
    root._metadata = {"source_hash": "stale-source-hash"}

    result = spec_change(root)
    assert result is root


def test_spec_change_returns_earliest_required_module_with_change():
    """Iteration order in ``all_required_modules`` is leaf-first; a change on
    the leaf must be reported in preference to one on ``middle``."""
    leaf = FakeModule("leaf")
    middle = FakeModule("middle", required_modules=[leaf])
    root = FakeModule("root", required_modules=[middle])

    # Both required modules have stale metadata; leaf comes first.
    leaf._metadata = {"source_hash": "stale-leaf"}
    middle._metadata = {"source_hash": "stale-middle"}
    root._metadata = _unchanged_metadata(root)

    result = spec_change(root)
    assert result is leaf


def test_spec_change_ignores_metadata_without_source_hash():
    leaf = FakeModule("leaf")
    root = FakeModule("root", required_modules=[leaf])

    leaf._metadata = {"unrelated": "field"}
    root._metadata = _unchanged_metadata(root)

    assert spec_change(root) is None


# -------------------------
# code_change
# -------------------------


def test_code_change_detects_change_in_top_module():
    leaf = FakeModule("leaf")
    root = FakeModule("root", required_modules=[leaf])

    root._metadata = {"required_modules_code_hash": "stale-code-hash"}

    result = code_change(root)
    assert result is leaf


def test_code_change_returns_none_when_hashes_match():
    leaf = FakeModule("leaf")
    middle = FakeModule("middle", required_modules=[leaf])
    root = FakeModule("root", required_modules=[middle])

    middle._metadata = _unchanged_metadata(middle)
    root._metadata = _unchanged_metadata(root)

    assert code_change(root) is None


def test_code_change_skips_leaf_modules_in_iteration():
    """A module with no required modules can't exhibit a required-modules-code
    change. The iteration skips such leaves without crashing on empty lists."""
    leaf = FakeModule("leaf")
    middle = FakeModule("middle", required_modules=[leaf])
    root = FakeModule("root", required_modules=[middle])

    # Only the top module's metadata records a stale code hash.
    middle._metadata = _unchanged_metadata(middle)
    root._metadata = {"required_modules_code_hash": "stale"}

    result = code_change(root)
    assert result is middle


def test_code_change_prefers_earliest_required_module():
    leaf = FakeModule("leaf")
    middle = FakeModule("middle", required_modules=[leaf])
    root = FakeModule("root", required_modules=[middle])

    # ``middle`` has a stale code hash; so does ``root``.  ``middle`` comes
    # first in ``all_required_modules``, so ``middle``'s previous module
    # (``leaf``) is reported as the changed code.
    middle._metadata = {"required_modules_code_hash": "stale-middle-code"}
    root._metadata = {"required_modules_code_hash": "stale-root-code"}

    result = code_change(root)
    assert result is leaf


# -------------------------
# detect_partial_rendering
# -------------------------


def _build_fresh_tree(last_rendered=(None, None)):
    """Build a root/middle/leaf tree with all metadata in-sync (no changes)."""
    leaf = FakeModule("leaf")
    middle = FakeModule("middle", required_modules=[leaf])
    root = FakeModule("root", required_modules=[middle], last_rendered=last_rendered)
    leaf._metadata = _unchanged_metadata(leaf)
    middle._metadata = _unchanged_metadata(middle)
    root._metadata = _unchanged_metadata(root)
    return root, middle, leaf


def test_detect_partial_rendering_returns_none_when_nothing_rendered():
    root, _, _ = _build_fresh_tree(last_rendered=(None, None))
    assert get_plain_module_render_state(root) is None


def test_detect_partial_rendering_no_changes_returns_last_rendered():
    root, _middle, leaf = _build_fresh_tree(last_rendered=("leaf", "1"))
    pr = get_plain_module_render_state(root)
    assert isinstance(pr, PlainModuleRenderState)
    assert pr.last_render_module is leaf
    assert pr.last_render_frid == "1"
    assert pr.change is None
    assert pr.change_type is None


def test_detect_partial_rendering_raises_when_last_rendered_module_unknown():
    root, _, _ = _build_fresh_tree(last_rendered=("phantom", "1"))
    with pytest.raises(ModuleDoesNotExistError, match="phantom"):
        get_plain_module_render_state(root)


def test_detect_partial_rendering_spec_change_on_earlier_module_wins():
    """A spec change on an earlier (required) module is reported via
    ``change``/``change_type``; ``last_render_module`` and ``last_render_frid``
    remain the ones reported by ``get_module_render_status``."""
    root, middle, leaf = _build_fresh_tree(last_rendered=("middle", "1"))
    leaf._metadata = {"source_hash": "stale-leaf"}  # leaf's spec changed

    pr = get_plain_module_render_state(root)
    assert pr.last_render_module is middle
    assert pr.change is leaf
    assert pr.change_type == "spec_change"
    assert pr.last_render_frid == "1"


def test_detect_partial_rendering_spec_change_on_top_module_is_reported():
    """A spec change on the top (root) module is reported on the
    ``PartialRender``; ``last_render_module``/``last_render_frid`` reflect the
    module that was last rendered."""
    root, _middle, leaf = _build_fresh_tree(last_rendered=("leaf", "1"))
    root._metadata = {"source_hash": "stale-root"}  # spec change on root

    pr = get_plain_module_render_state(root)
    assert pr.last_render_module is leaf
    assert pr.change is root
    assert pr.change_type == "spec_change"
    assert pr.last_render_frid == "1"


def test_detect_partial_rendering_code_change_wins_over_last_rendered():
    root, middle, leaf = _build_fresh_tree(last_rendered=("middle", "1"))
    middle._metadata = {
        "source_hash": middle.get_module_source_hash(),
        "required_modules_code_hash": "stale-code",
    }

    pr = get_plain_module_render_state(root)
    assert pr.last_render_module is middle
    assert pr.change is leaf
    assert pr.change_type == "code_change"
    assert pr.last_render_frid == "1"


def test_detect_partial_rendering_spec_and_code_changes_are_mutually_exclusive():
    """When both a spec change and a code change are detected, the
    ``code_change`` branch overrides if ``cc`` comes before (or equals) the
    current ``pr.change``. Here both point at ``leaf`` — spec_change returns
    the module whose spec is stale; code_change returns the previous module
    whose code has changed — so the code_change branch wins the tie."""
    root, middle, leaf = _build_fresh_tree(last_rendered=("middle", "1"))
    # Leaf has a spec change.
    leaf._metadata = {"source_hash": "stale-leaf"}
    # Middle records a stale code hash for its required module (leaf).
    middle._metadata = {
        "source_hash": middle.get_module_source_hash(),
        "required_modules_code_hash": "stale-middle-code",
    }

    pr = get_plain_module_render_state(root)
    assert pr.last_render_module is middle
    assert pr.change is leaf
    assert pr.change_type == "code_change"
    assert pr.last_render_frid == "1"


# -------------------------
# get_render_choices
# -------------------------


def _choice_types(choices):
    return [choice.choice_type for choice in choices.values()]


def _build_choices_tree(**root_kwargs):
    """leaf -> middle -> root, where root is the top module passed to
    ``get_render_choices``. ``root_kwargs`` configures the top module."""
    leaf = FakeModule("leaf")
    middle = FakeModule("middle", required_modules=[leaf])
    root = FakeModule("root", required_modules=[middle], **root_kwargs)
    return root, middle, leaf


# --- Block 1: primary-resume choices (only offered when nothing changed) ---


def test_render_choices_no_change_unrendered_module_offers_module_start():
    root, _, _ = _build_choices_tree(no_rendered_functionality=True)
    pr = PlainModuleRenderState(last_render_module=root, last_render_frid=None)

    choices = get_render_choices(root, pr)

    assert _choice_types(choices) == ["module_start", "quit"]
    assert choices["1"].module is root
    assert choices["1"].is_destructive is False


def test_render_choices_no_change_interrupted_module_offers_continue(monkeypatch):
    root, middle, _ = _build_choices_tree()
    root._next_frid = ("2", middle)
    monkeypatch.setattr(partial_rendering.plain_spec, "get_render_range_from", lambda frid, source: ["2", "3"])
    pr = PlainModuleRenderState(last_render_module=middle, last_render_frid="1")

    choices = get_render_choices(root, pr)

    assert _choice_types(choices) == ["continue_from_frid", "quit"]
    assert choices["1"].module is middle
    assert choices["1"].render_range == ["2", "3"]


def test_render_choices_no_change_fully_rendered_required_module_starts_next():
    root, middle, _ = _build_choices_tree()
    middle._fully_rendered = True
    pr = PlainModuleRenderState(last_render_module=middle, last_render_frid="9")

    choices = get_render_choices(root, pr)

    assert _choice_types(choices) == ["module_start", "quit"]
    assert choices["1"].module is root
    assert choices["1"].is_destructive is False


def test_render_choices_no_change_fully_rendered_top_module_rerenders_self():
    root, _, _ = _build_choices_tree(fully_rendered=True)
    pr = PlainModuleRenderState(last_render_module=root, last_render_frid="9")

    choices = get_render_choices(root, pr)

    assert _choice_types(choices) == ["module_start", "quit"]
    assert choices["1"].module is root
    assert choices["1"].is_destructive is True


# --- Block 2: a detected change suppresses the resume choice (the merge) ---


def test_render_choices_spec_change_in_first_module_offers_partial_and_full_restart(monkeypatch):
    """Change in the first (leaf) module: offer (1) the partial render and (2) a full restart
    of the affected module(s). The "re-render from first" option is deduped away because the
    leaf already is the first module."""
    root, _, leaf = _build_choices_tree(fully_rendered=True)
    monkeypatch.setattr(
        partial_rendering, "determine_partial_render_start", lambda pm: PartialRenderStart(module=leaf, frid="1")
    )
    monkeypatch.setattr(partial_rendering.plain_spec, "get_render_range_from", lambda frid, source: ["1", "2"])
    pr = PlainModuleRenderState(last_render_module=root, last_render_frid="9", change=leaf, change_type="spec_change")

    choices = get_render_choices(root, pr)

    # The stale "continue / re-render the current module" choice must NOT appear.
    assert "module_start" not in _choice_types(choices)
    assert "continue_from_frid" not in _choice_types(choices)
    assert _choice_types(choices) == ["render_from_change", "rerender_affected", "quit"]
    assert choices["1"].module is leaf
    assert choices["1"].render_range == ["1", "2"]
    assert choices["2"].module is leaf


def test_render_choices_spec_change_in_top_module_offers_partial_restart_and_full_reset(monkeypatch):
    """Change in the top module C at FR 3: offer (1) start from FR 3, (2) re-render C from
    scratch, and (3) rebuild everything from the first module — plus quit."""
    root, _, leaf = _build_choices_tree(fully_rendered=True)
    monkeypatch.setattr(
        partial_rendering, "determine_partial_render_start", lambda pm: PartialRenderStart(module=root, frid="3")
    )
    monkeypatch.setattr(partial_rendering.plain_spec, "get_render_range_from", lambda frid, source: ["3", "4"])
    pr = PlainModuleRenderState(last_render_module=root, last_render_frid="9", change=root, change_type="spec_change")

    choices = get_render_choices(root, pr)

    assert "module_start" not in _choice_types(choices)
    assert "continue_from_frid" not in _choice_types(choices)
    assert _choice_types(choices) == ["render_from_change", "rerender_affected", "rerender_from_first", "quit"]
    # (1) partial render from the changed functionality in C
    assert choices["1"].module is root
    assert choices["1"].render_range == ["3", "4"]
    # (2) re-render the affected module (C) from scratch
    assert choices["2"].module is root
    assert choices["2"].render_range is None
    assert choices["2"].is_destructive is True
    # (3) rebuild everything from the first module
    assert choices["3"].module is leaf


def test_render_choices_code_change_suppresses_resume_and_offers_rerender_affected():
    root, _, leaf = _build_choices_tree(fully_rendered=True)
    pr = PlainModuleRenderState(last_render_module=root, last_render_frid="9", change=leaf, change_type="code_change")

    choices = get_render_choices(root, pr)

    assert "module_start" not in _choice_types(choices)
    assert "continue_from_frid" not in _choice_types(choices)
    assert _choice_types(choices) == ["rerender_affected", "quit"]
    assert choices["1"].module is leaf


def test_render_choices_spec_change_no_partial_start_falls_back_to_full_rerenders(monkeypatch):
    """When no safe partial start exists (e.g. non-FR sections changed), the
    affected module and the first module are offered for a full re-render."""
    root, middle, leaf = _build_choices_tree(fully_rendered=True)
    monkeypatch.setattr(partial_rendering, "determine_partial_render_start", lambda pm: None)
    pr = PlainModuleRenderState(last_render_module=root, last_render_frid="9", change=middle, change_type="spec_change")

    choices = get_render_choices(root, pr)

    assert "module_start" not in _choice_types(choices)
    assert "continue_from_frid" not in _choice_types(choices)
    assert _choice_types(choices) == ["rerender_affected", "rerender_from_first", "quit"]
    assert choices["1"].module is middle
    assert choices["2"].module is leaf


def test_render_choices_appended_after_render_frontier_auto_continues(monkeypatch):
    """A new functionality appended after the last-rendered one is pure future work: offer
    only a non-destructive continue, so the renderer resumes without prompting the user."""
    root, _, _ = _build_choices_tree()  # not fully rendered: a new FR sits past the frontier
    root._next_frid = ("3", root)
    monkeypatch.setattr(
        partial_rendering, "determine_partial_render_start", lambda pm: PartialRenderStart(module=root, frid="3")
    )
    monkeypatch.setattr(partial_rendering.plain_spec, "get_render_range_from", lambda frid, source: ["3"])
    pr = PlainModuleRenderState(last_render_module=root, last_render_frid="2", change=root, change_type="spec_change")

    choices = get_render_choices(root, pr)

    assert _choice_types(choices) == ["continue_from_frid", "quit"]
    assert choices["1"].module is root
    assert choices["1"].render_range == ["3"]
    assert choices["1"].is_destructive is False


def test_render_choices_appended_in_required_module_still_prompts(monkeypatch):
    """Appending a functionality to a *required* module is not pure future work — it sits
    before the already-rendered top module, so a decision is still required."""
    root, middle, leaf = _build_choices_tree(fully_rendered=True)
    monkeypatch.setattr(
        partial_rendering, "determine_partial_render_start", lambda pm: PartialRenderStart(module=middle, frid="3")
    )
    monkeypatch.setattr(partial_rendering.plain_spec, "get_render_range_from", lambda frid, source: ["3"])
    pr = PlainModuleRenderState(last_render_module=root, last_render_frid="9", change=middle, change_type="spec_change")

    choices = get_render_choices(root, pr)

    assert "continue_from_frid" not in _choice_types(choices)
    assert _choice_types(choices) == ["render_from_change", "rerender_affected", "rerender_from_first", "quit"]
    assert choices["1"].module is middle


def test_render_choices_always_ends_with_quit():
    root, _, _ = _build_choices_tree(fully_rendered=True)
    pr = PlainModuleRenderState(last_render_module=root, last_render_frid="9")

    choices = get_render_choices(root, pr)

    last_key = list(choices.keys())[-1]
    assert choices[last_key].choice_type == "quit"
    assert choices[last_key].module is None


# -------------------------
# change_is_only_future_work
# -------------------------


def test_change_is_only_future_work_true_for_append_past_frontier():
    root, _, _ = _build_choices_tree()
    pr = PlainModuleRenderState(last_render_module=root, last_render_frid="2", change=root, change_type="spec_change")
    assert change_is_only_future_work(root, pr, PartialRenderStart(module=root, frid="3")) is True


def test_change_is_only_future_work_false_for_change_at_or_before_frontier():
    root, _, _ = _build_choices_tree()
    pr = PlainModuleRenderState(last_render_module=root, last_render_frid="3", change=root, change_type="spec_change")
    # Same frid (the changed functionality was already rendered) is not strictly after.
    assert change_is_only_future_work(root, pr, PartialRenderStart(module=root, frid="3")) is False
    assert change_is_only_future_work(root, pr, PartialRenderStart(module=root, frid="2")) is False


def test_change_is_only_future_work_false_for_change_in_earlier_module():
    root, middle, _ = _build_choices_tree()
    pr = PlainModuleRenderState(last_render_module=root, last_render_frid="9", change=middle, change_type="spec_change")
    assert change_is_only_future_work(root, pr, PartialRenderStart(module=middle, frid="3")) is False


def test_change_is_only_future_work_false_when_no_partial_start():
    root, _, _ = _build_choices_tree()
    pr = PlainModuleRenderState(last_render_module=root, last_render_frid="9")
    assert change_is_only_future_work(root, pr, None) is False
