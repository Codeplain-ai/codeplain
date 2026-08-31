"""Regression tests for linked-resource validation during --dry-run.

A linked resource must be a single text-based file. Rendering rejects binary
targets in ``file_utils.load_linked_resources`` (a failed UTF-8 decode raises
``UnsupportedResourceType``); dry-run must surface the same errors through
``plain2code.validate_linked_resources``, which reuses that exact mechanism.
"""

import pytest

from plain2code import validate_linked_resources
from plain2code_exceptions import UnsupportedResourceType
from plain_modules import PlainModule

PLAIN_TEMPLATE = """***definitions***

- :Widget: is an item managed by the application.
  - Its icon is [the widget resource](resources/{resource_name}).

***implementation reqs***

- Implementation should be in Python.

***functional specs***

- A :Widget: can be created.
"""


def _make_module(tmp_path, monkeypatch, resource_name, resource_bytes):
    resources_dir = tmp_path / "resources"
    resources_dir.mkdir()
    (resources_dir / resource_name).write_bytes(resource_bytes)
    (tmp_path / "binlink.plain").write_text(PLAIN_TEMPLATE.format(resource_name=resource_name))

    # Link targets are resolved relative to the current working directory at parse time.
    monkeypatch.chdir(tmp_path)
    return PlainModule("binlink.plain", str(tmp_path / "build"), [str(tmp_path)])


def test_dry_run_validation_rejects_binary_linked_resource(tmp_path, monkeypatch):
    module = _make_module(tmp_path, monkeypatch, "image.png", b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\xff\xfe\xfd")

    with pytest.raises(UnsupportedResourceType) as exc_info:
        validate_linked_resources(module)

    assert "resources/image.png" in str(exc_info.value)
    assert "binary file" in str(exc_info.value)


def test_dry_run_validation_accepts_text_file_with_unusual_extension(tmp_path, monkeypatch):
    module = _make_module(tmp_path, monkeypatch, "notes.xyz123", b"plain text content\n")

    validate_linked_resources(module)


def test_dry_run_validation_accepts_empty_file(tmp_path, monkeypatch):
    module = _make_module(tmp_path, monkeypatch, "empty.txt", b"")

    validate_linked_resources(module)
