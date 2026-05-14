import os
import tempfile

import pytest

from file_utils import get_existing_files_content, load_linked_resources, normalize_line_endings, open_from
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


def test_normalize_line_endings_windows_crlf():
    """Test that Windows CRLF line endings are normalized to LF"""
    text = "line1\r\nline2\r\nline3\r\n"
    result = normalize_line_endings(text)
    assert result == "line1\nline2\nline3\n"


def test_normalize_line_endings_mac_classic_cr():
    """Test that Mac Classic CR line endings are normalized to LF"""
    text = "line1\rline2\rline3\r"
    result = normalize_line_endings(text)
    assert result == "line1\nline2\nline3\n"


def test_normalize_line_endings_unix_lf():
    """Test that Unix LF line endings remain unchanged"""
    text = "line1\nline2\nline3\n"
    result = normalize_line_endings(text)
    assert result == "line1\nline2\nline3\n"


def test_normalize_line_endings_mixed():
    """Test that mixed line endings are all normalized to LF"""
    text = "line1\r\nline2\rline3\nline4\r\n"
    result = normalize_line_endings(text)
    assert result == "line1\nline2\nline3\nline4\n"


def test_get_existing_files_content_normalizes_line_endings(template_dir):
    """Test that get_existing_files_content normalizes line endings from files"""
    # Create a file with Windows line endings
    file_path = os.path.join(template_dir, "test.py")
    with open(file_path, "wb") as f:
        f.write(b"def hello():\r\n    return True\r\n")

    result = get_existing_files_content(template_dir, ["test.py"])

    # Should be normalized to LF
    assert result == {"test.py": "def hello():\n    return True\n"}
    # Verify it actually had CRLF before normalization
    with open(file_path, "rb") as f:
        raw_content = f.read()
    assert b"\r\n" in raw_content


def test_open_from_normalizes_line_endings(template_dir):
    """Test that open_from normalizes line endings from linked resources"""
    # Create a file with Windows line endings
    file_path = os.path.join(template_dir, "resource.md")
    with open(file_path, "wb") as f:
        f.write(b"# Header\r\nContent here\r\n")

    result = open_from([template_dir], "resource.md")

    # Should be normalized to LF
    assert result == "# Header\nContent here\n"
