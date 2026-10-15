import os
import tempfile

import pytest

import file_utils
from diff_utils import get_code_diff
from file_utils import get_existing_files_content, load_linked_resources, store_response_files
from plain2code_exceptions import UnsupportedBase64Content, UnsupportedResourceType
from plain2code_utils import MAX_BASE64_BLOB_LENGTH


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


def test_load_linked_resources_base64_blob_raises(template_dir):
    file_path = os.path.join(template_dir, "request.txt")
    with open(file_path, "w") as f:
        f.write("curl -d 'selfie_image=" + "A" * (MAX_BASE64_BLOB_LENGTH + 100) + "'")

    with pytest.raises(UnsupportedBase64Content) as exc_info:
        load_linked_resources([template_dir], [{"text": "Request", "target": "request.txt"}], "my_thing")

    assert "request.txt" in str(exc_info.value)
    assert "my_thing" in str(exc_info.value)


def test_load_linked_resources_small_base64_allowed(template_dir):
    file_path = os.path.join(template_dir, "token.txt")
    with open(file_path, "w") as f:
        f.write("token=" + "A" * (MAX_BASE64_BLOB_LENGTH - 100))

    result = load_linked_resources([template_dir], [{"text": "Token", "target": "token.txt"}], "my_thing")

    assert "token.txt" in result


def test_load_linked_resources_real_base64_image_raises(template_dir):
    # Real base64-encoded JPEG that made Gemini 3 fail, inlined into a curl example resource.
    sample_path = os.path.join(os.path.dirname(__file__), "data", "sample_base64_image.txt")
    with open(sample_path) as f:
        blob = f.read()

    file_path = os.path.join(template_dir, "face_match_request.txt")
    with open(file_path, "w") as f:
        f.write(f"curl -d 'selfie_image={blob}' https://example.com/face-match")

    with pytest.raises(UnsupportedBase64Content) as exc_info:
        load_linked_resources([template_dir], [{"text": "Request", "target": "face_match_request.txt"}], "face_match")

    assert "face_match_request.txt" in str(exc_info.value)
    assert str(len(blob)) in str(exc_info.value)


def test_store_response_files_writes_unicode_as_utf8(template_dir):
    """A non-cp1252 character (📍 U+1F4CD) is written as UTF-8, whatever the platform's default encoding."""
    content = "Location 📍 marker\n"
    store_response_files(template_dir, {"notes.md": content}, [])

    file_path = os.path.join(template_dir, "notes.md")
    with open(file_path, "rb") as f:
        assert f.read().decode("utf-8") == content


def _read_bytes(folder, file_name):
    """Returns a file's content exactly as stored on disk, without newline translation."""
    with open(os.path.join(folder, file_name), "rb") as f:
        return f.read().decode("utf-8")


@pytest.mark.parametrize(
    "content, expected",
    [
        ("class Foo\nend", "class Foo\nend\n"),
        ("class Foo\nend\n", "class Foo\nend\n"),
        ("class Foo\nend\n\n", "class Foo\nend\n\n"),
        ("class Foo\r\nend\r\n", "class Foo\r\nend\r\n"),
        ("", ""),
    ],
)
def test_store_response_files_ends_every_text_file_with_a_newline(template_dir, content, expected):
    """A missing final newline is added, as linters such as RuboCop, ESLint and Ruff require.

    An existing final newline is not doubled, and empty content stays empty.
    """
    store_response_files(template_dir, {"app/foo.rb": content}, [])

    assert _read_bytes(template_dir, "app/foo.rb") == expected


def test_store_response_files_hands_back_the_content_it_wrote(template_dir):
    """response_files holds the content as written, so diffs taken after storing match the disk.

    The fix journal and the conformance fixes handed to the unit tests fixer take such diffs.
    """
    response_files = {"app/foo.rb": "class Foo\nend", "app/bar.rb": "class Bar\nend\n"}

    store_response_files(template_dir, response_files, [])

    assert response_files == {"app/foo.rb": "class Foo\nend\n", "app/bar.rb": "class Bar\nend\n"}


def test_rewriting_a_file_without_its_final_newline_does_not_change_it(template_dir):
    """A rewrite that drops only the final newline leaves the file byte-identical and produces no diff."""
    existing_files = store_response_files(template_dir, {"app/foo.rb": "class Foo\nend\n"}, [])
    existing_files_content = get_existing_files_content(template_dir, existing_files)

    rewrite = {"app/foo.rb": "class Foo\nend"}
    store_response_files(template_dir, rewrite, existing_files)

    assert get_existing_files_content(template_dir, existing_files) == existing_files_content
    assert get_code_diff(rewrite, existing_files_content) == {}


def test_store_response_files_still_deletes_a_file_whose_content_is_none(template_dir):
    """None content still deletes the file and removes it from existing_files, and is not turned into a newline."""
    existing_files = store_response_files(template_dir, {"app/foo.rb": "class Foo\nend\n"}, [])

    response_files = {"app/foo.rb": None}
    store_response_files(template_dir, response_files, existing_files)

    assert not os.path.exists(os.path.join(template_dir, "app/foo.rb"))
    assert existing_files == []
    assert response_files == {"app/foo.rb": None}


def test_store_response_files_writes_the_same_bytes_where_text_mode_writes_crlf(template_dir, monkeypatch):
    """Files hold exactly the normalized content even where text mode writes CRLF line endings (native Windows)."""
    # Imported here: isort 8 and isort 9 disagree on which section a top-level `import _pyio` belongs to.
    import _pyio

    # Python's reference io (_pyio) takes its line separator from os.linesep, so here it translates like Windows.
    monkeypatch.setattr(os, "linesep", "\r\n")
    monkeypatch.setattr(file_utils, "open", _pyio.open, raising=False)
    with _pyio.open(os.path.join(template_dir, "probe.txt"), "w") as probe:
        probe.write("\n")
    assert _read_bytes(template_dir, "probe.txt") == "\r\n"

    response_files = {"app/foo.rb": "class Foo\nend", "app/bar.rb": "class Bar\r\nend\r\n"}
    store_response_files(template_dir, response_files, [])

    assert _read_bytes(template_dir, "app/foo.rb") == response_files["app/foo.rb"] == "class Foo\nend\n"
    assert _read_bytes(template_dir, "app/bar.rb") == response_files["app/bar.rb"] == "class Bar\r\nend\r\n"
