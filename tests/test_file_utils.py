import os
import tempfile

import pytest

from file_utils import store_response_files


@pytest.fixture
def template_dir():
    with tempfile.TemporaryDirectory() as d:
        yield d


def test_store_response_files_writes_unicode_as_utf8(template_dir):
    # Content with a non-cp1252 character (📍 U+1F4CD) must be written as UTF-8
    # regardless of the platform's default text encoding (e.g. cp1252 on Windows).
    content = "Location 📍 marker"
    store_response_files(template_dir, {"notes.md": content}, [])

    file_path = os.path.join(template_dir, "notes.md")
    with open(file_path, "rb") as f:
        assert f.read().decode("utf-8") == content
