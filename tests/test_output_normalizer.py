"""Tests for the terminal output normalizer.

The fixtures under `tests/fixtures/terminal_output/` are real recordings, not synthesized
escape soup: each one is the verbatim byte stream a real tool wrote to the master side of
a pseudoterminal allocated by this project's own PTY backend, at 120x40 under
`TERM=xterm-256color`.

Every fixture case asserts the rendered result against a committed golden file *and* the
compression ratio, so a regression that reintroduces noise shows up as a number, and one
that deletes output shows up as a diff — needles and an upper ratio bound alone would pass
for a normalizer that dropped nearly everything.
"""

import re
from pathlib import Path

import pytest

from render_machine.output_normalizer import (
    MAX_COMBINING_MARKS,
    MAX_SEQUENCE_BYTES,
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


def read_golden(name: str) -> str:
    """The rendering committed alongside the recording, byte for byte."""
    return (FIXTURES / name).with_suffix(".normalized").read_text(encoding="utf-8")


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
def test_recorded_output_matches_the_committed_rendering(name, max_ratio, present, absent):
    """Equality, because an upper ratio bound alone rewards deleting output."""
    assert normalize(read_fixture(name)).text() == read_golden(name)


@pytest.mark.parametrize("name, max_ratio, present, absent", FIXTURE_CASES)
def test_recorded_output_is_compressed_to_what_a_terminal_would_show(name, max_ratio, present, absent):
    raw = read_fixture(name)
    rendered = normalize(raw).text().encode("utf-8")  # bytes to bytes, so the ratio is one unit
    ratio = len(rendered) / len(raw)
    assert ratio <= max_ratio, f"{name} normalized to {ratio:.3f} of its raw size, above {max_ratio}"


@pytest.mark.parametrize("name, max_ratio, present, absent", FIXTURE_CASES)
def test_chunk_boundaries_do_not_change_the_rendering(name, max_ratio, present, absent):
    """The reader feeds whatever `read()` returns, so a split sequence must still render."""
    raw = read_fixture(name)
    assert normalize(raw, chunk_size=1).text() == normalize(raw, chunk_size=len(raw) + 1).text()


@pytest.mark.parametrize("control", [b"\x18", b"\x1a"], ids=["CAN", "SUB"])
def test_text_after_a_ground_state_abort_survives(control):
    """pyte drops the rest of the `feed()` a CAN or SUB arrives in, so the rendering used to
    depend on where the OS split the read."""
    raw = b"before" + control + b"after"

    assert normalize(raw, chunk_size=len(raw) + 1).text().strip() == "beforeafter"
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


def test_translate_newlines_renders_a_pipe_stream_of_bare_linefeeds():
    normalizer = OutputNormalizer(columns=20, lines=5, translate_newlines=True)
    normalizer.feed(b"one\ntwo\nthree\n")

    assert normalizer.text() == "one\ntwo\nthree\n"


def test_translate_newlines_leaves_a_crlf_stream_unchanged():
    normalizer = OutputNormalizer(columns=20, lines=5, translate_newlines=True)
    normalizer.feed(b"one\r\ntwo\r\nno newline here")

    assert normalizer.text() == "one\ntwo\nno newline here\n"


def test_translate_newlines_counts_the_bytes_the_target_wrote():
    normalizer = OutputNormalizer(columns=20, lines=5, translate_newlines=True)
    normalizer.feed(b"a\nb\n")

    assert normalizer.fed_bytes == 4


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


POISON_SEQUENCE = b"\x1b[1;2;3z"  # the one sequence the parser is made to reject below


def render_with_a_rejected_sequence(monkeypatch, raw: bytes, chunk_size: int) -> OutputNormalizer:
    normalizer = OutputNormalizer(columns=40, lines=5)
    original = normalizer._stream.feed

    def feed(data):
        if data == POISON_SEQUENCE:
            raise ValueError("malformed")
        original(data)

    monkeypatch.setattr(normalizer._stream, "feed", feed)
    for offset in range(0, len(raw), chunk_size):
        normalizer.feed(raw[offset : offset + chunk_size])
    return normalizer


def test_a_parser_failure_costs_one_sequence_and_rendering_continues(monkeypatch):
    """A malformed sequence must cost itself, never the reader that feeds it."""
    raw = b"before " + POISON_SEQUENCE + b"after\r\n"
    normalizer = render_with_a_rejected_sequence(monkeypatch, raw, chunk_size=len(raw))

    assert normalizer.parse_failures == 1
    assert normalizer.text() == "before after\n"


def test_parser_failure_recovery_does_not_depend_on_the_read_boundaries(monkeypatch):
    """The reader feeds whatever `read()` returns, so recovery cannot cost the rest of it."""
    raw = b"before " + POISON_SEQUENCE + b"after\r\n"
    whole = render_with_a_rejected_sequence(monkeypatch, raw, chunk_size=len(raw))
    split = render_with_a_rejected_sequence(monkeypatch, raw, chunk_size=1)
    mid = render_with_a_rejected_sequence(monkeypatch, raw, chunk_size=9)

    assert whole.text() == split.text() == mid.text()
    assert whole.parse_failures == split.parse_failures == mid.parse_failures == 1


def test_an_unterminated_osc_string_is_bounded_and_its_bytes_stay_visible():
    """pyte would hold every byte of it; the guard caps its buffer and, past the cap,
    treats the stream as plain output again — a string cut off mid-write must not
    swallow the transcript that follows it."""
    normalizer = OutputNormalizer(columns=40, lines=5)
    normalizer.feed(b"\x1b]0;")
    for _ in range(200):
        normalizer.feed(b"A" * 4096)  # a title that never terminates
    normalizer.feed(b"\x07after\r\n")

    assert normalizer._guard.pending_bytes <= MAX_SEQUENCE_BYTES
    assert normalizer.bounded_sequences == 1
    text = normalizer.text()
    assert text.endswith("after\n")
    assert "AAAA" in text  # the abandoned string's bytes render instead of vanishing
    assert len(normalizer._screen.title) <= MAX_SEQUENCE_BYTES


def test_an_unterminated_csi_parameter_is_bounded_and_draining_continues():
    normalizer = OutputNormalizer(columns=40, lines=5)
    normalizer.feed(b"\x1b[")
    for _ in range(200):
        normalizer.feed(b"9" * 4096)  # a parameter no terminal would ever finish reading
    normalizer.feed(b"m")
    normalizer.feed(b"still here\r\n")

    assert normalizer._guard.pending_bytes <= MAX_SEQUENCE_BYTES
    assert normalizer.bounded_sequences == 1
    assert normalizer.parse_failures == 0
    assert normalizer.text() == "still here\n"


def test_an_oversized_osc_string_is_abandoned_and_never_becomes_metadata():
    """Whether its terminator ever arrives cannot be known at the cap, so an oversized
    string is reclaimed as plain output either way — it must never grow the title."""
    normalizer = OutputNormalizer(columns=40, lines=5)
    normalizer.feed(b"\x1b]0;short title\x07")
    normalizer.feed(b"\x1b]0;" + b"B" * (MAX_SEQUENCE_BYTES * 4) + b"\x07")
    normalizer.feed(b"work goes on\r\n")

    assert normalizer._screen.title == "short title"
    assert normalizer.bounded_sequences == 1
    assert normalizer.text().endswith("work goes on\n")


def test_repeated_combining_marks_do_not_grow_one_cell_without_bound():
    normalizer = OutputNormalizer(columns=40, lines=5)
    marks = ("́" * 200).encode("utf-8")  # combining acute accents, one cell's worth of state
    normalizer.feed(b"a")
    for _ in range(500):
        normalizer.feed(marks)
    normalizer.feed(b"\r\nsecond line\r\n")

    first_line = normalizer.text().splitlines()[0]
    assert len(first_line) <= MAX_COMBINING_MARKS + 1
    assert first_line.startswith("á")  # the first mark still composes with the letter
    assert normalizer.text().splitlines()[1] == "second line"


def test_a_trailing_partial_utf8_sequence_is_finalized_as_a_replacement_character():
    normalizer = OutputNormalizer(columns=20, lines=3)
    normalizer.feed("hé".encode("utf-8")[:-1])  # the stream ends mid-character

    assert normalizer.text() == "h\n"

    normalizer.finalize()
    normalizer.finalize()  # idempotent: shutdown paths can overlap

    assert normalizer.text() == "h�\n"


def test_finalizing_a_complete_stream_changes_nothing():
    normalizer = OutputNormalizer(columns=20, lines=3)
    normalizer.feed("héllo\r\n".encode("utf-8"))
    before = normalizer.text()

    normalizer.finalize()

    assert normalizer.text() == before == "héllo\n"


def test_fed_bytes_counts_every_byte_handed_to_the_parser():
    raw = read_fixture("spinner.raw")
    assert normalize(raw).fed_bytes == len(raw)


def test_a_truncated_control_string_does_not_swallow_the_output_after_it():
    """A tool killed mid-title-write must not blind the transcript: whatever followed the
    unterminated introducer is reclaimed as plain output when the stream ends."""
    normalizer = OutputNormalizer(columns=120, lines=5)
    normalizer.feed(b"start\r\n")
    normalizer.feed(b"\x1b]0;title")  # cut off before its terminator ever arrives
    normalizer.feed(b"FAILED: assertion xyz\r\n")
    normalizer.finalize()

    text = normalizer.text()
    assert "start" in text
    assert "FAILED: assertion xyz" in text


def test_can_aborts_a_control_string_and_rendering_resumes():
    normalizer = OutputNormalizer(columns=40, lines=5)
    normalizer.feed(b"\x1b]0;half a title\x18after\r\n")

    assert normalizer._screen.title == ""  # an aborted string is discarded, not dispatched
    assert normalizer.text() == "after\n"


def test_an_escape_that_is_not_a_terminator_ends_the_string_and_starts_a_sequence():
    normalizer = OutputNormalizer(columns=40, lines=5)
    normalizer.feed(b"\x1b]0;half a title\x1b[31mred text\r\n")

    assert normalizer._screen.title == ""  # the string was exited, never dispatched
    assert normalizer.text() == "red text\n"


def test_a_second_escape_restarts_the_sequence_rather_than_corrupting_it():
    normalizer = OutputNormalizer(columns=40, lines=5)
    normalizer.feed(b"\x1b\x1b[31mred\r\n")  # the first ESC led nowhere

    assert normalizer.text() == "red\n"


def test_an_escape_pair_inside_a_string_exits_it_and_the_next_sequence_still_parses():
    normalizer = OutputNormalizer(columns=40, lines=5)
    normalizer.feed(b"\x1b]0;discard\x1b\x1b[31mred\r\n")

    assert normalizer._screen.title == ""
    assert normalizer.text() == "red\n"
