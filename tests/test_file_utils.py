import os
import tempfile

import pytest

from file_utils import load_linked_resources, store_response_files
from plain2code_exceptions import UnsupportedResourceType


@pytest.fixture
def template_dir():
    with tempfile.TemporaryDirectory() as d:
        yield d


def test_load_linked_resources_text_file(template_dir):
    file_path = os.path.join(template_dir, "notes.md")
    with open(file_path, "w") as f:
        f.write("# hello")

    result = load_linked_resources([template_dir], [{"text": "Notes", "target": "notes.md"}], "my_thing")

    assert result == {"notes.md": "# hello"}


def test_load_linked_resources_binary_file_raises_unsupported_resource_type(template_dir):
    file_path = os.path.join(template_dir, "icon.png")
    with open(file_path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\xff\xfe\xfd")

    with pytest.raises(UnsupportedResourceType) as exc_info:
        load_linked_resources([template_dir], [{"text": "Icon", "target": "icon.png"}], "my_thing")

    assert "icon.png" in str(exc_info.value)
    assert "binary file" in str(exc_info.value)
    assert "my_thing" in str(exc_info.value)


def test_load_linked_resources_missing_file_raises_file_not_found(template_dir):
    with pytest.raises(FileNotFoundError):
        load_linked_resources([template_dir], [{"text": "Missing", "target": "missing.md"}], "my_thing")


def test_store_response_files_writes_unicode_as_utf8(template_dir):
    # Content with a non-cp1252 character (📍 U+1F4CD) must be written as UTF-8
    # regardless of the platform's default text encoding (e.g. cp1252 on Windows).
    content = "Location 📍 marker"
    store_response_files(template_dir, {"notes.md": content}, [])

    file_path = os.path.join(template_dir, "notes.md")
    with open(file_path, "rb") as f:
        assert f.read().decode("utf-8") == content
