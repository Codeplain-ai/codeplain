import os
import tempfile

import pytest

from file_utils import load_linked_resources
from plain2code_exceptions import UnsupportedResourceType


@pytest.fixture
def template_dir():
    with tempfile.TemporaryDirectory() as d:
        yield d


def test_load_linked_resources_text_file(template_dir):
    file_path = os.path.join(template_dir, "notes.md")
    with open(file_path, "w") as f:
        f.write("# hello")

    result = load_linked_resources([template_dir], [{"text": "Notes", "target": "notes.md"}])

    assert result == {"notes.md": "# hello"}


def test_load_linked_resources_binary_file_raises_unsupported_resource_type(template_dir):
    file_path = os.path.join(template_dir, "icon.png")
    with open(file_path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\xff\xfe\xfd")

    with pytest.raises(UnsupportedResourceType) as exc_info:
        load_linked_resources([template_dir], [{"text": "Icon", "target": "icon.png"}])

    assert "icon.png" in str(exc_info.value)
    assert "binary file" in str(exc_info.value)


def test_load_linked_resources_missing_file_raises_file_not_found(template_dir):
    with pytest.raises(FileNotFoundError):
        load_linked_resources([template_dir], [{"text": "Missing", "target": "missing.md"}])
