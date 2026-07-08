"""Tests for the structured trace records written to codeplain.log."""

import logging

import pytest

from plain2code_trace import PREVIEW_CHARS, preview, summarize_args, trace


@pytest.fixture
def codeplain_records(caplog):
    with caplog.at_level(logging.DEBUG, logger="codeplain"):
        yield caplog


def test_trace_emits_grepable_one_line_record(codeplain_records):
    trace("tool", name="grep", count=3, flag=True, skipped=None, msg="hello world")

    assert len(codeplain_records.records) == 1
    message = codeplain_records.records[0].getMessage()
    assert message.startswith("[tool] ")
    assert "name=grep" in message
    assert "count=3" in message
    assert "flag=True" in message
    assert 'msg="hello world"' in message
    assert "skipped" not in message  # None fields are omitted
    assert "\n" not in message


def test_trace_truncates_and_flattens_large_values(codeplain_records):
    trace("api", body="line one\nline two\n" + "x" * 5000)

    message = codeplain_records.records[0].getMessage()
    assert "\n" not in message
    assert "line one line two" in message
    assert "chars)" in message  # explicit truncation marker
    assert len(message) < PREVIEW_CHARS + 200


def test_trace_formats_lists_and_durations(codeplain_records):
    trace("agent", tools=["read_file", "grep"], duration_s=1.2345, empty=[])

    message = codeplain_records.records[0].getMessage()
    assert "tools=read_file,grep" in message
    assert "duration_s=1.2" in message
    assert "empty=-" in message


def test_preview_collapses_whitespace_and_caps_length():
    assert preview("a\n  b\t c") == "a b c"
    long_text = "y" * 1000
    result = preview(long_text)
    assert len(result) < 300
    assert "…(+760 chars)" in result


def test_file_formatters_handle_lazily_formatted_records():
    """Regression: the formatters re-interpolated msg % args after getMessage(),
    crashing on any record logged lazily (logger.debug("%s", value)) — which is
    exactly how trace() logs."""
    from unittest.mock import MagicMock

    from plain2code_logger import ElapsedTimeFormatter, IndentedFormatter

    record = logging.LogRecord("codeplain", logging.DEBUG, __file__, 1, "[%s] %s", ("tool", "name=grep"), None)
    run_state = MagicMock(render_time_accumulated=0, last_render_start_timestamp=0)

    formatted = ElapsedTimeFormatter(run_state).format(record)
    assert "[tool] name=grep" in formatted

    record = logging.LogRecord("codeplain", logging.DEBUG, __file__, 1, "[%s] %s", ("tool", "name=grep"), None)
    formatted = IndentedFormatter("%(message)s").format(record)
    assert formatted == "[tool] name=grep"


def test_summarize_args_hides_bulky_values():
    summary = summarize_args({"file_path": "src/app.py", "content": "z" * 5000})

    assert "file_path=src/app.py" in summary
    assert "content=<5000 chars>" in summary
    assert "zzzz" not in summary
