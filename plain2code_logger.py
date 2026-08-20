import copy
import logging

from event_bus import EventBus
from plain2code_events import LogMessageEmitted
from plain2code_state import RunState

LOGGER_NAME = "codeplain"

# Attach a NullHandler so that log records emitted before setup_logging() configures
# the real handlers (e.g. during --dry-run, --status, --full-plain, or early parse
# errors) are not printed to stderr by logging.lastResort. Without this, console.error()
# / console.warning() — which both log the message and print it via rich — would show the
# same message twice: once in plain text (from lastResort) and once styled (from rich).
logging.getLogger(LOGGER_NAME).addHandler(logging.NullHandler())

FILE_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
FILE_LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def _with_indented_message(record, indent: str):
    """A copy of the record whose continuation lines are indented.

    One record is handed to every attached handler in turn, so a formatter that
    rewrites `record.msg` in place is rewriting it for the handlers that come after
    it too — a headless render logging to both stdout and a file would indent each
    continuation line twice, once per formatter. Copying keeps each handler's
    formatting local to that handler.
    """
    indented = copy.copy(record)
    indented.msg = record.getMessage().replace("\n", "\n" + indent)
    indented.args = None  # getMessage() already interpolated them into msg
    return indented


class IndentedFormatter(logging.Formatter):
    def __init__(self, fmt=None, datefmt=None, indent=16):
        super().__init__(fmt=fmt, datefmt=datefmt)
        self._indent = " " * indent

    def format(self, record):
        return super().format(_with_indented_message(record, self._indent))


class ElapsedTimeFormatter(logging.Formatter):
    """Formatter that adds elapsed time since render started, accounting for pauses."""

    def __init__(self, run_state: RunState, fmt: str = "%(elapsed_time)s %(levelname)s %(name)s: %(message)s"):
        super().__init__(fmt=fmt)
        self.run_state = run_state

    def format(self, record):
        # Calculate elapsed time the same way as LoggingHandler does for the TUI
        try:
            offset_seconds = self.run_state.get_live_render_time()
        except Exception:
            # If RunState is not available or there's any error, default to 00:00:00
            offset_seconds = 0

        hours = offset_seconds // 3600
        minutes = (offset_seconds % 3600) // 60
        seconds = offset_seconds % 60
        elapsed_time = f"[{hours:02d}:{minutes:02d}:{seconds:02d}]"

        # Continuation lines line up under the message, past the timestamp column.
        indented = _with_indented_message(record, " " * len(elapsed_time + " "))
        indented.elapsed_time = elapsed_time

        return super().format(indented)


class LoggingHandler(logging.Handler):
    def __init__(self, event_bus: EventBus, run_state: RunState):
        super().__init__()
        self.event_bus = event_bus
        self.run_state = run_state

    def emit(self, record):
        try:
            offset_seconds = self.run_state.get_live_render_time()

            hours = offset_seconds // 3600
            minutes = (offset_seconds % 3600) // 60
            seconds = offset_seconds % 60
            timestamp = f"{hours:02d}:{minutes:02d}:{seconds:02d}"

            event = LogMessageEmitted(
                logger_name=record.name,
                level=record.levelname,
                message=record.getMessage(),
                timestamp=timestamp,
                log_color=getattr(record, "log_color", None),
            )
            self.event_bus.publish(event)
        except RuntimeError:
            # We're going to get this crash after the TUI app is closed (forcefully).
            # NOTE: This should be more thought out.
            pass
        except Exception:
            self.handleError(record)


class CrashLogHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)

    def dump_to_file(self, filepath, formatter=None):
        if not self.records:
            return False

        try:
            with open(filepath, "w", encoding="utf-8") as f:
                for record in self.records:
                    if formatter:
                        msg = formatter.format(record)
                    else:
                        msg = self.format(record)
                    f.write(msg + "\n")
            return True
        except Exception:
            return False


def dump_crash_logs(args, run_state: RunState, formatter=None):
    """Dump buffered logs to file if CrashLogHandler is present."""
    if args.log_to_file:
        return

    if formatter is None:
        formatter = IndentedFormatter(FILE_LOG_FORMAT, datefmt=FILE_LOG_DATE_FORMAT, indent=len("YYYY-MM-DD HH:MM:SS "))

    root_logger = logging.getLogger(LOGGER_NAME)
    crash_handler = next((h for h in root_logger.handlers if isinstance(h, CrashLogHandler)), None)

    if crash_handler and args.filename:
        crash_handler.dump_to_file(args.log_file_name, formatter)


NARRATION_HANDLER_NAME = "headless-narration"


def stop_narrating() -> None:
    """Detaches the headless stdout sink once the render is over.

    The summary prints with Rich and bypasses logging, so a still-attached sink reports
    every failure twice.
    """
    root_logger = logging.getLogger(LOGGER_NAME)
    for handler in list(root_logger.handlers):
        if handler.get_name() == NARRATION_HANDLER_NAME:
            root_logger.removeHandler(handler)
            handler.close()
