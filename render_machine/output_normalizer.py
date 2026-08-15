"""Renders a terminal byte stream instead of stripping bytes out of it.

Under a PTY `isatty()` is true, so toolchains emit colour, cursor movement, progress-line
rewrites and full-screen repaints. Deleting those bytes would delete the *instruction*
without performing the *operation*: every stale frame would survive and be concatenated. A
tool repainting a status block 200 times would yield 200 stacked copies where a terminal
shows one.

So the normalizer runs a VT state machine (pyte) over the raw bytes and emits what a
terminal would have shown: the lines that scrolled off, then the final screen. `\\r\\n`
collapses to `\\n` and no SGR survives, because the output is rendered from the screen
buffer rather than filtered out of the stream.

The parser also runs live in the reader, because terminals answer queries: a target may
emit `ESC[5n`, `ESC[6n` or `ESC[c` and block until the terminal replies. `reply_handler`
receives those replies; a normalizer constructed without one simply renders.
"""

import collections
import threading
from typing import Callable, Deque, Dict, List, Optional

import pyte
from pyte.screens import Char, Margins

from render_machine.terminal_process import TERMINAL_COLUMNS, TERMINAL_ROWS

# Head and tail of the scrolled-off transcript. Blind truncation would drop whichever end
# happens to matter; a failing run needs its invocation (head) and its error (tail).
SCROLLBACK_HEAD_LINES = 300
SCROLLBACK_TAIL_LINES = 1700

# Private DEC modes that swap in the alternate screen buffer.
ALTERNATE_SCREEN_MODES = (47, 1047, 1049)

# Query kinds reported to the reply handler.
QUERY_DEVICE_STATUS = "device-status"
QUERY_CURSOR_POSITION = "cursor-position"
QUERY_DEVICE_ATTRIBUTES = "device-attributes"


def render_line(line: Dict[int, Char], columns: int) -> str:
    """One buffer line as plain text.

    The cell after a double-width character holds an empty stub, so a plain join over the
    row reproduces what the screen shows without consulting character widths.
    """
    return "".join(line[x].data for x in range(columns)).rstrip()


def _trim_trailing_blanks(lines: List[str]) -> List[str]:
    while lines and not lines[-1]:
        lines.pop()
    return lines


class _RetainedLines:
    """Keeps the head and the tail of the scrolled-off transcript."""

    def __init__(self, head_lines: int, tail_lines: int) -> None:
        self._head: List[str] = []
        self._tail: Deque[str] = collections.deque(maxlen=tail_lines)
        self._head_lines = head_lines
        self.total = 0

    def append(self, line: str) -> None:
        self.total += 1
        if len(self._head) < self._head_lines:
            self._head.append(line)
        else:
            self._tail.append(line)

    def lines(self) -> List[str]:
        omitted = self.total - len(self._head) - len(self._tail)
        if omitted <= 0:
            return self._head + list(self._tail)
        return self._head + [f"...[{omitted} lines omitted]..."] + list(self._tail)


class _RenderingScreen(pyte.Screen):
    """A pyte screen that retains what scrolls off and answers device queries.

    pyte keeps only the visible screen and its `write_process_input()` is a no-op, so both
    behaviours are supplied here.
    """

    def __init__(
        self,
        columns: int,
        lines: int,
        scrollback: _RetainedLines,
        reply_handler: Optional[Callable[[str, bytes], None]],
    ) -> None:
        # Set before super().__init__, which resets the screen and can reach these.
        self._scrollback = scrollback
        self._reply_handler = reply_handler
        self._alternate = False
        self._query_kind = QUERY_DEVICE_ATTRIBUTES
        super().__init__(columns, lines)

    # ------------------------------------------------------------------ scrollback

    def index(self) -> None:
        """Overloaded to retain the line the scroll pushes off the top."""
        top, bottom = self.margins or Margins(0, self.lines - 1)
        if self.cursor.y == bottom and not self._alternate:
            self._scrollback.append(render_line(self.buffer[top], self.columns))
        super().index()

    # ------------------------------------------------------------ alternate screen

    def set_mode(self, *modes: int, **kwargs) -> None:
        if kwargs.get("private") and any(mode in ALTERNATE_SCREEN_MODES for mode in modes):
            self._switch_screen(alternate=True)
        super().set_mode(*modes, **kwargs)

    def reset_mode(self, *modes: int, **kwargs) -> None:
        if kwargs.get("private") and any(mode in ALTERNATE_SCREEN_MODES for mode in modes):
            self._switch_screen(alternate=False)
        super().reset_mode(*modes, **kwargs)

    def _switch_screen(self, alternate: bool) -> None:
        """Flushes the outgoing screen into the scrollback and starts the incoming one clear.

        A terminal restores the primary screen verbatim and discards the alternate one. The
        transcript is a linear log instead, so each switch appends the frame that is leaving
        and continues below it — chronological, and still free of every repaint that frame
        replaced.
        """
        if alternate == self._alternate:
            return
        self._alternate = alternate
        for line in _trim_trailing_blanks(self.screen_lines()):
            self._scrollback.append(line)
        self.buffer.clear()
        self.dirty.update(range(self.lines))
        self.cursor_position()

    # --------------------------------------------------------------- device queries

    def report_device_status(self, mode: int = 0, **kwargs) -> None:
        if kwargs.get("private"):
            return  # DECDSR, which this terminal does not claim to implement
        self._query_kind = QUERY_DEVICE_STATUS if mode == 5 else QUERY_CURSOR_POSITION
        super().report_device_status(mode)

    def report_device_attributes(self, mode: int = 0, **kwargs) -> None:
        self._query_kind = QUERY_DEVICE_ATTRIBUTES
        super().report_device_attributes(mode, **kwargs)

    def write_process_input(self, data: str) -> None:
        """pyte's reply hook. The reply is terminal protocol, never caller input."""
        handler = self._reply_handler
        if handler is None:
            return
        handler(self._query_kind, data.encode("utf-8"))

    # ------------------------------------------------------------------- rendering

    def screen_lines(self) -> List[str]:
        return [render_line(self.buffer[y], self.columns) for y in range(self.lines)]


class OutputNormalizer:
    """Renders a target's terminal output and answers the queries it emits.

    Fed by the reader thread and read by the foreground, so both entry points take one
    lock. `feed()` never raises: a malformed sequence must not take the reader down.
    """

    def __init__(
        self,
        columns: int = TERMINAL_COLUMNS,
        lines: int = TERMINAL_ROWS,
        head_lines: int = SCROLLBACK_HEAD_LINES,
        tail_lines: int = SCROLLBACK_TAIL_LINES,
        reply_handler: Optional[Callable[[str, bytes], None]] = None,
    ) -> None:
        self._lock = threading.Lock()
        self._scrollback = _RetainedLines(head_lines, tail_lines)
        self._screen = _RenderingScreen(columns, lines, self._scrollback, reply_handler)
        self._stream = pyte.ByteStream(self._screen)
        self.parse_failures = 0
        self.fed_bytes = 0

    def resize(self, columns: int, lines: int) -> None:
        """Matches the parser to the terminal the target was actually given."""
        with self._lock:
            self._screen.resize(lines, columns)

    def feed(self, data: bytes) -> None:
        if not data:
            return
        with self._lock:
            self.fed_bytes += len(data)
            try:
                self._stream.feed(data)
            except Exception:
                # pyte reinitializes its parser before propagating, so the next chunk is
                # parsed from a clean state. Rendering continues with what was already
                # drawn rather than costing the reader its life.
                self.parse_failures += 1

    def text(self) -> str:
        """The rendered scrollback followed by the final screen, as plain text."""
        with self._lock:
            lines = self._scrollback.lines() + self._screen.screen_lines()
        while lines and not lines[0]:
            del lines[0]
        _trim_trailing_blanks(lines)
        return "\n".join(lines) + "\n" if lines else ""
