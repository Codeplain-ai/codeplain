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
import unicodedata
from typing import Callable, Deque, Dict, List, Optional

import pyte
from pyte.screens import Char, Margins

from render_machine.terminal_process import TERMINAL_COLUMNS, TERMINAL_ROWS

# Head and tail of the scrolled-off transcript. Blind truncation would drop whichever end
# happens to matter; a failing run needs its invocation (head) and its error (tail).
SCROLLBACK_HEAD_LINES = 300
SCROLLBACK_TAIL_LINES = 1700

# Caps on the parser state a target can grow. Both are far above anything a terminal
# renders and far below anything that costs the reader its memory.
MAX_SEQUENCE_BYTES = 4096
MAX_COMBINING_MARKS = 8

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


# Framing states, and the bytes that move between them.
_GROUND, _ESCAPE, _INTERMEDIATE, _CSI, _STRING = range(5)
_ESC = 0x1B
_BEL = 0x07
_CAN = 0x18
_SUB = 0x1A
_STRING_INTRODUCERS = frozenset(b"]PX^_")  # OSC, DCS, SOS, PM, APC
_ESCAPE_INTERMEDIATES = frozenset(b"#%()")  # each takes exactly one more byte


class _SequenceGuard:
    """Frames a byte stream into plain runs and whole escape sequences, with a size cap.

    pyte 0.8.2 accumulates an unterminated OSC string or CSI parameter inside its parser
    coroutine without any bound, so a target that writes `ESC ] 0 ;` and then never
    terminates it grows the reader's memory for as long as it runs. An in-progress sequence
    is held here instead: the buffer is this class's own, it is capped, and the remainder of
    an oversized sequence is dropped rather than parsed.

    Framing never changes what the parser sees — the same bytes arrive in the same order.
    It only decides where one `feed()` call ends, which is what makes a parse failure cost
    one sequence instead of the rest of an OS-sized read.
    """

    def __init__(self, max_sequence_bytes: int = MAX_SEQUENCE_BYTES) -> None:
        self._max_sequence_bytes = max_sequence_bytes
        self._state = _GROUND
        self._pending = bytearray()
        self._dropping = False
        self._after_escape = False
        self.dropped = 0

    @property
    def pending_bytes(self) -> int:
        return len(self._pending)

    def frame(self, data: bytes) -> List[bytes]:
        """The units to hand the parser: plain runs and complete escape sequences."""
        units: List[bytes] = []
        index = 0
        length = len(data)
        while index < length:
            if self._state == _GROUND:
                start = data.find(_ESC, index)
                if start < 0:
                    units.append(data[index:])
                    break
                if start > index:
                    units.append(data[index:start])
                self._state = _ESCAPE
                self._pending += b"\x1b"
                index = start + 1
            else:
                index = self._consume(data, index, units)
        return units

    def _consume(self, data: bytes, index: int, units: List[bytes]) -> int:
        length = len(data)
        while index < length and self._state != _GROUND:
            byte = data[index]
            index += 1
            if byte in (_CAN, _SUB):
                # CAN and SUB abort a sequence in any state, like a real parser; an
                # aborted sequence is discarded, never handed to the parser.
                self._reset()
                continue
            if self._state == _STRING and self._after_escape and byte != 0x5C:
                # Only ESC \ terminates a string, but any other ESC-introduced byte still
                # ends it: the ESC begins a new escape sequence, exactly as a real
                # parser's exit from its string state does.
                self._reset()
                self._state = _ESCAPE
                self._pending += b"\x1b"
            if self._state == _ESCAPE and byte == _ESC and not self._dropping:
                # ESC restarts the escape state: the previous ESC led nowhere and is
                # dropped, and whatever follows this one is parsed as its own sequence.
                self._pending.clear()
                self._pending += b"\x1b"
                continue
            if not self._dropping and len(self._pending) >= self._max_sequence_bytes:
                self.dropped += 1
                if self._state == _STRING:
                    # A control string this long is abandoned rather than dropped to a
                    # terminator that may never come: a stream cut mid-string would
                    # otherwise swallow the remainder of the transcript. Its payload is
                    # reclaimed as plain output, so nothing the target wrote is lost.
                    units.append(bytes(self._pending[2:]))
                    self._reset()
                    return index - 1
                # Nothing renders a sequence this long, so the rest of it is parsed by
                # nobody and the buffer that held it is released here.
                self._dropping = True
                self._pending.clear()
            if not self._dropping:
                self._pending.append(byte)
            if self._ends_sequence(byte):
                if not self._dropping:
                    units.append(bytes(self._pending))
                self._reset()
        return index

    def flush(self) -> bytes:
        """The payload of an unterminated control string, reclaimed as plain output.

        Called at end of stream: a target cut off mid-string never sends the terminator,
        and whatever followed the introducer would otherwise vanish from the transcript.
        Incomplete sequences of every other kind stay dropped — they carry no payload.
        """
        payload = bytes(self._pending[2:]) if self._state == _STRING and not self._dropping else b""
        self._reset()
        return payload

    def _ends_sequence(self, byte: int) -> bool:
        if self._state == _ESCAPE:
            if byte == 0x5B:  # [
                self._state = _CSI
            elif byte in _STRING_INTRODUCERS:
                self._state = _STRING
            elif byte in _ESCAPE_INTERMEDIATES:
                self._state = _INTERMEDIATE
            else:
                return True
            return False
        if self._state == _INTERMEDIATE:
            return True
        if self._state == _CSI:
            return 0x40 <= byte <= 0x7E  # the final byte; parameters and controls are lower
        if self._after_escape:  # only ESC \ terminates a string; ESC anything else does not
            self._after_escape = False
            return byte == 0x5C
        if byte == _ESC:
            self._after_escape = True
            return False
        return byte == _BEL

    def _reset(self) -> None:
        self._state = _GROUND
        self._pending.clear()
        self._dropping = False
        self._after_escape = False


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
        max_combining_marks: int = MAX_COMBINING_MARKS,
    ) -> None:
        # Set before super().__init__, which resets the screen and can reach these.
        self._scrollback = scrollback
        self._reply_handler = reply_handler
        self._alternate = False
        self._query_kind = QUERY_DEVICE_ATTRIBUTES
        self._max_combining_marks = max_combining_marks
        self._combining_run = 0
        super().__init__(columns, lines)

    # -------------------------------------------------------------------- drawing

    def draw(self, data: str) -> None:
        """Caps how many combining marks one cell can accumulate.

        pyte appends every zero-width combining mark to the previous cell's string, so a
        target emitting them in a loop grows one cell without bound. A run past the cap is
        dropped: no terminal renders it, and nothing else bounds it.
        """
        if data.isascii():  # the common case, and no combining mark is ASCII
            self._combining_run = 0
            super().draw(data)
            return
        super().draw(self._cap_combining_marks(data))

    def _cap_combining_marks(self, data: str) -> str:
        kept: List[str] = []
        run = self._combining_run
        for char in data:
            if unicodedata.combining(char):
                run += 1
                if run > self._max_combining_marks:
                    continue
            else:
                run = 0  # a character that advances the cursor starts the next cell's run
            kept.append(char)
        self._combining_run = run
        return "".join(kept)

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
    lock. `feed()` never raises: a malformed sequence must not take the reader down. Every
    piece of parser state a target can grow — an unterminated sequence, one cell's
    combining marks — is capped, because the reader is the process's only drainer.
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
        self._guard = _SequenceGuard()
        self._finalized = False
        self.parse_failures = 0
        self.fed_bytes = 0

    @property
    def bounded_sequences(self) -> int:
        """Escape sequences dropped for exceeding the size cap."""
        return self._guard.dropped

    def resize(self, columns: int, lines: int) -> None:
        """Matches the parser to the terminal the target was actually given."""
        with self._lock:
            self._screen.resize(lines, columns)

    def feed(self, data: bytes) -> None:
        if not data:
            return
        with self._lock:
            self.fed_bytes += len(data)
            for unit in self._guard.frame(data):
                try:
                    self._stream.feed(unit)
                except Exception:
                    # pyte reinitializes its parser before propagating, so the next unit is
                    # parsed from a clean state. The guard hands over one sequence at a
                    # time, so a malformed one costs itself rather than the rest of the
                    # read — and never the reader that feeds it.
                    self.parse_failures += 1

    def finalize(self) -> None:
        """Ends the stream: reclaims an unterminated control string's payload as plain
        output, then flushes the parser's decoder. Idempotent.

        A trailing incomplete UTF-8 sequence sits in pyte's incremental decoder until it is
        finalized, so without this it never reaches the screen and vanishes from the
        transcript instead of rendering as U+FFFD. `utf8_decoder` is pyte 0.8.2's decoder
        attribute and is reached defensively.
        """
        with self._lock:
            if self._finalized:
                return
            self._finalized = True
            leftover = self._guard.flush()
            if leftover:
                try:
                    self._stream.feed(leftover)
                except Exception:
                    self.parse_failures += 1
            decoder = getattr(self._stream, "utf8_decoder", None)
            if decoder is None:
                return
            try:
                tail = decoder.decode(b"", final=True)
                if tail:
                    pyte.Stream.feed(self._stream, tail)  # already text, so not ByteStream.feed
            except Exception:
                self.parse_failures += 1

    def text(self) -> str:
        """The rendered scrollback followed by the final screen, as plain text."""
        with self._lock:
            lines = self._scrollback.lines() + self._screen.screen_lines()
        while lines and not lines[0]:
            del lines[0]
        _trim_trailing_blanks(lines)
        return "\n".join(lines) + "\n" if lines else ""
