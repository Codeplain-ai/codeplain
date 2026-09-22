import os
import tempfile

import pytest

from diff_utils import get_code_diff
from file_utils import get_existing_files_content, store_response_files


@pytest.fixture
def template_dir():
    with tempfile.TemporaryDirectory() as d:
        yield d


def test_store_response_files_writes_unicode_as_utf8(template_dir):
    # Content with a non-cp1252 character (📍 U+1F4CD) must be written as UTF-8
    # regardless of the platform's default text encoding (e.g. cp1252 on Windows).
    content = "Location 📍 marker\n"
    store_response_files(template_dir, {"notes.md": content}, [])

    file_path = os.path.join(template_dir, "notes.md")
    with open(file_path, "rb") as f:
        assert f.read().decode("utf-8") == content


def _read_bytes(folder, file_name):
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
    # Linters gate on a final newline (RuboCop Layout/TrailingEmptyLines, ESLint eol-last, Ruff W292), so a
    # whole-file rewrite that comes back without one must not reach the disk that way.
    store_response_files(template_dir, {"app/foo.rb": content}, [])

    assert _read_bytes(template_dir, "app/foo.rb") == expected


def test_store_response_files_hands_back_the_content_it_wrote(template_dir):
    # Callers diff the response files against the build folder after storing them (fix journal, conformance
    # fixes handed to the unit tests fixer), so the response files must match what is on disk.
    response_files = {"app/foo.rb": "class Foo\nend", "app/bar.rb": "class Bar\nend\n"}

    store_response_files(template_dir, response_files, [])

    assert response_files == {"app/foo.rb": "class Foo\nend\n", "app/bar.rb": "class Bar\nend\n"}


def test_rewriting_a_file_without_its_final_newline_does_not_change_it(template_dir):
    existing_files = store_response_files(template_dir, {"app/foo.rb": "class Foo\nend\n"}, [])
    existing_files_content = get_existing_files_content(template_dir, existing_files)

    rewrite = {"app/foo.rb": "class Foo\nend"}
    store_response_files(template_dir, rewrite, existing_files)

    assert get_existing_files_content(template_dir, existing_files) == existing_files_content
    assert get_code_diff(rewrite, existing_files_content) == {}


def test_store_response_files_still_deletes_a_file_whose_content_is_none(template_dir):
    existing_files = store_response_files(template_dir, {"app/foo.rb": "class Foo\nend\n"}, [])

    response_files = {"app/foo.rb": None}
    store_response_files(template_dir, response_files, existing_files)

    assert not os.path.exists(os.path.join(template_dir, "app/foo.rb"))
    assert existing_files == []
    assert response_files == {"app/foo.rb": None}
