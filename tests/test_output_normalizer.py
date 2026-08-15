"""Tests for the terminal output normalizer.

The fixtures under `tests/fixtures/terminal_output/` are real recordings, not synthesized
escape soup: each one is the verbatim byte stream a real tool wrote to the master side of
a pseudoterminal allocated by this project's own PTY backend, at 120x40 under
`TERM=xterm-256color`.

Every fixture case asserts the rendered result *and* the compression ratio, so a
regression that reintroduces noise shows up as a number rather than as a diff nobody
reads.
"""

import re
from pathlib import Path

import pytest

from render_machine.output_normalizer import (
    QUERY_CURSOR_POSITION,
    QUERY_DEVICE_ATTRIBUTES,
    QUERY_DEVICE_STATUS,
    OutputNormalizer,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "terminal_output"

# What stripping bytes out of the stream would leave behind, used to show the difference
# between deleting the instruction and performing the operation.
STRIP_PATTERN = re.compile(rb"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b[@-Z\\-_]")


def strip_escapes(raw: bytes) -> str:
    return STRIP_PATTERN.sub(b"", raw).decode("utf-8", "replace")


def normalize(raw: bytes, chunk_size: int = 512, **kwargs) -> OutputNormalizer:
    normalizer = OutputNormalizer(**kwargs)
    for offset in range(0, len(raw), chunk_size):
        normalizer.feed(raw[offset : offset + chunk_size])
    return normalizer


def read_fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


# name, max compression ratio, expected present, expected absent
FIXTURE_CASES = [
    pytest.param(
        "npm_install.raw",
        0.10,
        ["added 69 packages"],
        ["⠹"],  # every braille spinner frame is erased by the frame after it
        id="npm-install",
    ),
    pytest.param(
        "pytest_color.raw",
        0.65,
        ["1 failed, 4 passed", "AssertionError", "test_reports_a_failure"],
        [],
        id="pytest-colour",
    ),
    pytest.param(
        "spinner.raw",
        0.03,
        ["[####################] 100% done", "installed 60 packages"],
        ["downloading package-30", "downloading package-59"],
        id="progress-rewrite",
    ),
    pytest.param(
        "fullscreen.raw",
        0.10,
        ["frame 11", "BUILD FAILED: suite-03 case 7 timed out", "suite-08"],
        ["frame 00", "frame 05", "frame 10"],
        id="full-screen-repaint",
    ),
    pytest.param(
        "nohup_build.raw",
        0.30,
        ["stdout is a terminal, colour enabled", "compiled 12 modules", "warning"],
        ["compiling module 05"],
        id="nohup-detached",
    ),
]


@pytest.mark.parametrize("name, max_ratio, present, absent", FIXTURE_CASES)
def test_recorded_output_renders_to_plain_text(name, max_ratio, present, absent):
    raw = read_fixture(name)
    normalizer = normalize(raw)
    text = normalizer.text()

    assert normalizer.parse_failures == 0
    assert "\x1b" not in text, "an escape sequence survived rendering"
    assert "\r" not in text, "a carriage return survived rendering"
    for needle in present:
        assert needle in text, f"{needle!r} missing from:\n{text}"
    for needle in absent:
        assert needle not in text, f"{needle!r} should have been overwritten:\n{text}"


@pytest.mark.parametrize("name, max_ratio, present, absent", FIXTURE_CASES)
def test_recorded_output_is_compressed_to_what_a_terminal_would_show(name, max_ratio, present, absent):
    raw = read_fixture(name)
    ratio = len(normalize(raw).text()) / len(raw)
    assert ratio <= max_ratio, f"{name} normalized to {ratio:.3f} of its raw size, above {max_ratio}"


@pytest.mark.parametrize("name, max_ratio, present, absent", FIXTURE_CASES)
def test_chunk_boundaries_do_not_change_the_rendering(name, max_ratio, present, absent):
    """The reader feeds whatever `read()` returns, so a split sequence must still render."""
    raw = read_fixture(name)
    assert normalize(raw, chunk_size=1).text() == normalize(raw, chunk_size=len(raw) + 1).text()


def test_stripping_keeps_every_repaint_that_rendering_collapses():
    """The case stripping cannot handle: a tool that repaints the whole screen in place."""
    raw = read_fixture("fullscreen.raw")
    stripped = strip_escapes(raw)
    rendered = normalize(raw).text()

    assert stripped.count("BUILD DASHBOARD") == 12
    assert rendered.count("BUILD DASHBOARD") == 1
    assert len(rendered) < len(stripped) / 8


def test_a_progress_line_rewrite_collapses_to_its_last_frame():
    raw = read_fixture("spinner.raw")
    stripped = strip_escapes(raw)
    rendered = normalize(raw).text()

    assert stripped.count("downloading package-") == 60
    assert "downloading package-" not in rendered
    assert rendered.count("installed 60 packages") == 1


def test_scrollback_keeps_the_head_and_the_tail_of_a_long_run():
    raw = b"".join(f"line {index:04d}\r\n".encode() for index in range(1000))
    text = normalize(raw, head_lines=5, tail_lines=7).text()
    lines = text.splitlines()

    assert lines[:5] == [f"line {index:04d}" for index in range(5)]
    assert lines[5].startswith("...[") and lines[5].endswith("lines omitted]...")
    assert lines[-1] == "line 0999"
    assert len(lines) < 60, "retention must cap the transcript, not just trim its tail"


def test_the_final_screen_is_kept_whole_alongside_the_retained_scrollback():
    raw = b"".join(f"line {index:04d}\r\n".encode() for index in range(100))
    text = normalize(raw, lines=10, head_lines=3, tail_lines=3).text()
    lines = text.splitlines()

    assert lines[:3] == ["line 0000", "line 0001", "line 0002"]
    assert "...[" in lines[3]
    assert lines[-1] == "line 0099"


def test_cursor_movement_and_erase_are_performed_rather_than_deleted():
    normalizer = OutputNormalizer(columns=20, lines=5)
    normalizer.feed(b"first\r\nsecond\r\nthird\r\n")
    normalizer.feed(b"\x1b[3A\x1b[Kreplaced\r\n")  # up three lines, erase it, rewrite

    assert normalizer.text() == "replaced\nsecond\nthird\n"


def test_a_repaint_from_the_home_position_leaves_one_frame():
    normalizer = OutputNormalizer(columns=20, lines=4)
    for frame in range(30):
        normalizer.feed(f"\x1b[H\x1b[2Jframe {frame}\r\nstill working\r\n".encode())

    assert normalizer.text() == "frame 29\nstill working\n"


def test_the_alternate_screen_is_flushed_in_order_and_left_clear():
    normalizer = OutputNormalizer(columns=40, lines=6)
    normalizer.feed(b"primary one\r\nprimary two\r\n")
    normalizer.feed(b"\x1b[?1049h")
    for frame in range(1, 6):
        normalizer.feed(f"\x1b[H\x1b[2Jalt frame {frame}\r\n".encode())
    normalizer.feed(b"\x1b[?1049l")
    normalizer.feed(b"back on the primary\r\n")

    assert normalizer.text() == "primary one\nprimary two\nalt frame 5\nback on the primary\n"


def test_output_is_kept_when_the_target_never_leaves_the_alternate_screen():
    normalizer = OutputNormalizer(columns=40, lines=6)
    normalizer.feed(b"\x1b[?1049h\x1b[H\x1b[2Jonly frame\r\n")

    assert normalizer.text() == "only frame\n"


def test_crlf_collapses_and_a_trailing_partial_line_is_kept():
    normalizer = OutputNormalizer(columns=20, lines=5)
    normalizer.feed(b"one\r\ntwo\r\nno newline here")

    assert normalizer.text() == "one\ntwo\nno newline here\n"


def test_nothing_fed_renders_to_nothing():
    normalizer = OutputNormalizer(columns=20, lines=5)
    normalizer.feed(b"")

    assert normalizer.text() == ""


def test_split_utf8_across_chunks_renders_one_character():
    normalizer = OutputNormalizer(columns=20, lines=3)
    encoded = "héllo wörld".encode("utf-8")
    for index in range(len(encoded)):
        normalizer.feed(encoded[index : index + 1])

    assert normalizer.text() == "héllo wörld\n"


def test_device_queries_are_answered_as_a_terminal_answers_them():
    replies = []
    normalizer = OutputNormalizer(columns=20, lines=5, reply_handler=lambda kind, data: replies.append((kind, data)))
    normalizer.feed(b"abc\x1b[6n")
    normalizer.feed(b"\x1b[5n")
    normalizer.feed(b"\x1b[c")

    assert replies == [
        (QUERY_CURSOR_POSITION, b"\x1b[1;4R"),
        (QUERY_DEVICE_STATUS, b"\x1b[0n"),
        (QUERY_DEVICE_ATTRIBUTES, b"\x1b[?6c"),
    ]


def test_the_cursor_position_report_follows_the_rendered_cursor():
    replies = []
    normalizer = OutputNormalizer(columns=20, lines=5, reply_handler=lambda kind, data: replies.append((kind, data)))
    normalizer.feed(b"one\r\ntwo\r\nthr\x1b[6n")

    assert replies == [(QUERY_CURSOR_POSITION, b"\x1b[3;4R")]


def test_a_query_leaves_no_trace_in_the_rendered_text():
    replies = []
    normalizer = OutputNormalizer(columns=20, lines=5, reply_handler=lambda kind, data: replies.append((kind, data)))
    normalizer.feed(b"before\x1b[6n\x1b[5n\x1b[cafter\r\n")

    assert replies
    assert normalizer.text() == "beforeafter\n"


def test_a_normalizer_without_a_reply_handler_only_renders():
    normalizer = OutputNormalizer(columns=20, lines=5)
    normalizer.feed(b"quiet\x1b[6n\x1b[c\r\n")

    assert normalizer.text() == "quiet\n"


def test_a_private_device_status_request_is_not_answered():
    replies = []
    normalizer = OutputNormalizer(columns=20, lines=5, reply_handler=lambda kind, data: replies.append((kind, data)))
    normalizer.feed(b"x\x1b[?6n\r\n")

    assert replies == []
    assert normalizer.text() == "x\n"


def test_a_parser_failure_is_counted_and_rendering_continues(monkeypatch):
    """A malformed stream must cost a chunk, never the reader that feeds it."""
    normalizer = OutputNormalizer(columns=20, lines=5)
    normalizer.feed(b"before\r\n")

    original = normalizer._stream.feed
    calls = []

    def failing_feed(data):
        calls.append(data)
        raise ValueError("malformed")

    monkeypatch.setattr(normalizer._stream, "feed", failing_feed)
    normalizer.feed(b"poison")
    monkeypatch.setattr(normalizer._stream, "feed", original)
    normalizer.feed(b"after\r\n")

    assert calls == [b"poison"]
    assert normalizer.parse_failures == 1
    assert normalizer.text() == "before\nafter\n"


def test_fed_bytes_counts_every_byte_handed_to_the_parser():
    raw = read_fixture("spinner.raw")
    assert normalize(raw).fed_bytes == len(raw)
