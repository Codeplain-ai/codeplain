"""Unit tests for colored log messages (console color param, log records, and event forwarding)."""

import logging

import plain2code_logger
from event_bus import EventBus
from plain2code_console import RETRY_COLOR, SUCCESS_COLOR, Plain2CodeConsole
from plain2code_events import LogMessageEmitted
from plain2code_logger import LoggingHandler
from plain2code_state import RunState


class RecordCapturingHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)


def make_console_with_capture():
    console = Plain2CodeConsole()
    console.quiet = True
    handler = RecordCapturingHandler()
    logger = logging.getLogger(plain2code_logger.LOGGER_NAME)
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    return console, handler


class TestConsoleColorParam:
    """The color param must color terminal output only; log records carry plain text plus a log_color hint."""

    def teardown_method(self):
        logger = logging.getLogger(plain2code_logger.LOGGER_NAME)
        for handler in list(logger.handlers):
            if isinstance(handler, RecordCapturingHandler):
                logger.removeHandler(handler)

    def test_info_with_color_logs_plain_text_and_color_hint(self):
        console, handler = make_console_with_capture()
        console.info("↻ Script failed. Retrying...", color=RETRY_COLOR)

        assert len(handler.records) == 1
        record = handler.records[0]
        assert record.levelno == logging.INFO
        assert record.getMessage() == "↻ Script failed. Retrying..."
        assert record.log_color == RETRY_COLOR

    def test_debug_with_color_keeps_debug_level(self):
        console, handler = make_console_with_capture()
        console.debug("↻ Network error on attempt 1/4. Retrying in 3 seconds...", color=RETRY_COLOR)

        record = handler.records[0]
        assert record.levelno == logging.DEBUG
        assert record.log_color == RETRY_COLOR

    def test_color_never_appears_in_logged_message(self):
        console, handler = make_console_with_capture()
        console.info("✓ All scripts passed.", color=SUCCESS_COLOR)

        message = handler.records[0].getMessage()
        assert "[#" not in message
        assert "[/" not in message

    def test_no_color_defaults_to_none(self):
        console, handler = make_console_with_capture()
        console.info("plain info")

        assert handler.records[0].log_color is None


class TestTerminalTextRendersVerbatim:
    """
    Message text must reach the terminal unchanged, and must not influence its own
    styling. Colors are still applied but they are chosen by the call, never parsed
    out of the text:
     - Rich markup tags must not be interpreted
     - the repr highlighter must not restyle brackets and numbers
     - emoji shortcodes must not be substituted by glyphs
    """

    def test_markup_closing_tag_prints_verbatim(self):
        console = Plain2CodeConsole()
        with console.capture() as capture:
            console.error("Error: [/closing] tag")
        # markup=True raises MarkupError on an unmatched closing tag
        assert "[/closing]" in capture.get()

    def test_markup_style_tag_prints_verbatim(self):
        console = Plain2CodeConsole()
        with console.capture() as capture:
            console.error("Error: [bold]important text")
        # markup=True silently consumes the tag and styles the rest
        assert "[bold]" in capture.get()

    def test_bracketed_number_prints_verbatim(self, monkeypatch):
        # Rich emits no ANSI unless it believes it is writing to a colored terminal
        # (FORCE_COLOR alone is not enough: TERM=dumb still resolves to no colors)
        monkeypatch.setenv("FORCE_COLOR", "1")
        monkeypatch.setenv("TERM", "xterm-256color")
        console = Plain2CodeConsole()
        assert console.color_system is not None  # should fail test immediately if no colors

        with console.capture() as capture:
            console.error("Error: [Errno 8] failed")
        # highlight=True breaks this up with colour codes
        assert "[Errno 8]" in capture.get()

    def test_concept_name_matching_emoji_prints_verbatim(self):
        console = Plain2CodeConsole()
        with console.capture() as capture:
            console.error("Syntax error: Invalid concept: :Package:")
        assert ":Package:" in capture.get()
        assert "📦" not in capture.get()


class TestPrintHelpersDoNotSubstituteEmoji:
    """
    Same rule as TestTerminalTextRendersVerbatim, for the two helpers that bypass _log_and_print.
    Only emoji is covered here, markup and highlighting still apply to them.
    """

    def test_print_list_does_not_substitute_emoji(self):
        console = Plain2CodeConsole()
        with console.capture() as capture:
            console.print_list(["The :Package: is a concept"])
        assert ":Package:" in capture.get()

    def test_print_files_does_not_substitute_emoji_in_tree_labels(self):
        console = Plain2CodeConsole()
        with console.capture() as capture:
            console.print_files("Files added:", "build", {":Package:.py": "x"})
        assert ":Package:" in capture.get()


class TestLoggingHandlerForwardsColor:
    """LoggingHandler must forward the log_color record attribute into LogMessageEmitted."""

    def _emit_and_capture(self, log_call):
        run_state = RunState("test.plain")
        event_bus = EventBus()
        received = []
        event_bus.subscribe(LogMessageEmitted, received.append)

        logger = logging.getLogger(plain2code_logger.LOGGER_NAME)
        handler = LoggingHandler(event_bus, run_state)
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)
        try:
            log_call(logger)
        finally:
            logger.removeHandler(handler)
        return received

    def test_color_hint_is_forwarded(self):
        received = self._emit_and_capture(lambda logger: logger.info("↻ retrying", extra={"log_color": RETRY_COLOR}))

        assert len(received) == 1
        assert received[0].message == "↻ retrying"
        assert received[0].log_color == RETRY_COLOR

    def test_missing_color_hint_defaults_to_none(self):
        received = self._emit_and_capture(lambda logger: logger.info("plain message"))

        assert len(received) == 1
        assert received[0].log_color is None
