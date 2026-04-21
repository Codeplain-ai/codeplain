"""Tests for the partial rendering logic.

These tests use lightweight fake ``PlainModule``-like objects to exercise the
pure logic in ``partial_rendering`` without depending on the filesystem or a
real ``PlainModule``. The ``FakeModule`` exposes the small surface the module
under test relies on: ``module_name``, ``required_modules``,
``all_required_modules``, ``load_module_metadata``, ``get_module_source_hash``,
``get_module_code_hash`` and (for ``detect_partial_rendering``)
``get_last_rendered_frid``.
"""

import pytest

# Import ``plain_file`` first — it pulls in ``file_utils`` which transitively
# imports ``plain_modules``; starting the import graph at ``plain_file``
# sidesteps a circular-import failure that occurs when tests import
# ``partial_rendering`` (and therefore ``plain_modules``) first.
import plain_file  # noqa: F401
from partial_rendering import PartialRender, code_change, detect_partial_rendering, module_comes_before, spec_change
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
    ):
        self.module_name = module_name
        self.required_modules = list(required_modules or [])
        self._metadata = metadata
        self._source_hash = source_hash if source_hash is not None else f"src-{module_name}"
        self._code_hash = code_hash if code_hash is not None else f"code-{module_name}"
        self._last_rendered = last_rendered

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

    def get_last_rendered_frid(self):
        return self._last_rendered


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

    assert module_comes_before([a, b, c], a, b) is True
    assert module_comes_before([a, b, c], b, a) is False
    assert module_comes_before([a, b, c], b, c) is True


def test_module_comes_before_raises_when_neither_found():
    a = FakeModule("a")
    b = FakeModule("b")
    missing1 = FakeModule("missing1")
    missing2 = FakeModule("missing2")

    with pytest.raises(Exception, match="not found"):
        module_comes_before([a, b], missing1, missing2)


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
    assert result is root


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
    assert result is root


def test_code_change_prefers_earliest_required_module():
    leaf = FakeModule("leaf")
    middle = FakeModule("middle", required_modules=[leaf])
    root = FakeModule("root", required_modules=[middle])

    # ``middle`` has a stale code hash; so does ``root``.  ``middle`` comes
    # first in ``all_required_modules`` so it wins.
    middle._metadata = {"required_modules_code_hash": "stale-middle-code"}
    root._metadata = {"required_modules_code_hash": "stale-root-code"}

    result = code_change(root)
    assert result is middle


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
    assert detect_partial_rendering(root) is None


def test_detect_partial_rendering_no_changes_returns_last_rendered():
    root, _middle, leaf = _build_fresh_tree(last_rendered=("leaf", "1"))
    pr = detect_partial_rendering(root)
    assert isinstance(pr, PartialRender)
    assert pr.last_render_module is leaf
    assert pr.last_render_frid == "1"
    assert pr.change is None
    assert pr.change_type is None


def test_detect_partial_rendering_raises_when_last_rendered_module_unknown():
    root, _, _ = _build_fresh_tree(last_rendered=("phantom", "1"))
    with pytest.raises(ModuleDoesNotExistError, match="phantom"):
        detect_partial_rendering(root)


def test_detect_partial_rendering_spec_change_on_earlier_module_wins():
    """A spec change on an earlier (required) module is reported via
    ``change``/``change_type``; ``last_render_module`` and ``last_render_frid``
    remain the ones reported by ``get_last_rendered_frid``."""
    root, middle, leaf = _build_fresh_tree(last_rendered=("middle", "1"))
    leaf._metadata = {"source_hash": "stale-leaf"}  # leaf's spec changed

    pr = detect_partial_rendering(root)
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

    pr = detect_partial_rendering(root)
    assert pr.last_render_module is leaf
    assert pr.change is root
    assert pr.change_type == "spec_change"
    assert pr.last_render_frid == "1"


def test_detect_partial_rendering_code_change_wins_over_last_rendered():
    root, middle, _leaf = _build_fresh_tree(last_rendered=("middle", "1"))
    middle._metadata = {
        "source_hash": middle.get_module_source_hash(),
        "required_modules_code_hash": "stale-code",
    }

    pr = detect_partial_rendering(root)
    assert pr.last_render_module is middle
    assert pr.change is middle
    assert pr.change_type == "code_change"
    assert pr.last_render_frid == "1"


def test_detect_partial_rendering_spec_and_code_changes_are_mutually_exclusive():
    """When both a spec change and a code change are detected, the
    ``code_change`` branch only overrides if ``cc`` comes before the current
    ``pr.change``. Here leaf (spec) precedes middle (code) in
    ``all_required_modules``, so the spec change on leaf wins."""
    root, middle, leaf = _build_fresh_tree(last_rendered=("middle", "1"))
    # Leaf has a spec change.
    leaf._metadata = {"source_hash": "stale-leaf"}
    # Middle has a code change.
    middle._metadata = {
        "source_hash": middle.get_module_source_hash(),
        "required_modules_code_hash": "stale-middle-code",
    }

    pr = detect_partial_rendering(root)
    assert pr.last_render_module is middle
    assert pr.change is leaf
    assert pr.change_type == "spec_change"
    assert pr.last_render_frid == "1"
