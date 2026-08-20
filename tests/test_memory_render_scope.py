"""Tests for how memory is scoped to a render rather than to a module.

Two decisions are pinned here: every module in a requires chain shares one store, and a
full render starts clean while a partial render keeps what earlier functionalities
established.
"""

import os
import tempfile
from types import SimpleNamespace

import pytest

from memory_management import MemoryMode
from module_renderer import ModuleRenderer
from plain_modules import PlainModule, get_render_memory_folder


@pytest.fixture
def tmp_build_folder():
    with tempfile.TemporaryDirectory() as build:
        yield build


@pytest.fixture
def solo_module(get_test_data_path, tmp_build_folder):
    return PlainModule("pr_solo.plain", tmp_build_folder, [get_test_data_path("data/partial_rendering")])


def make_renderer(module, render_range, memory_mode=MemoryMode.ALL.value):
    return ModuleRenderer(
        codeplainAPI=None,
        plain_module=module,
        render_choice=None,
        render_range=render_range,
        args=SimpleNamespace(memory_mode=memory_mode),
        run_state=SimpleNamespace(render_id="test-render-id"),
        event_bus=None,
    )


def seed_record(module):
    record_path = os.path.join(get_render_memory_folder(module.build_folder), "conformance-abc-01.json")
    os.makedirs(os.path.dirname(record_path), exist_ok=True)
    with open(record_path, "w") as record_file:
        record_file.write("{}")
    return record_path


def test_store_is_rooted_above_the_modules(solo_module, tmp_build_folder):
    renderer = make_renderer(solo_module, render_range=None)

    assert renderer.memory_store.memory_folder == os.path.join(tmp_build_folder, ".memory")


def test_full_render_starts_from_an_empty_store(solo_module):
    record_path = seed_record(solo_module)

    make_renderer(solo_module, render_range=None).clear_memory_if_full_render()

    assert not os.path.exists(record_path)


def test_partial_render_keeps_what_earlier_functionalities_established(solo_module):
    record_path = seed_record(solo_module)

    make_renderer(solo_module, render_range=["2"]).clear_memory_if_full_render()

    assert os.path.exists(record_path)


def test_memory_mode_reaches_the_store(solo_module):
    renderer = make_renderer(solo_module, render_range=None, memory_mode=MemoryMode.REFUTED.value)

    assert renderer.memory_store.memory_mode is MemoryMode.REFUTED
