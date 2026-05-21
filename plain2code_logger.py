import logging
import os
import time
from typing import Optional

from event_bus import EventBus
from plain2code_events import LogMessageEmitted
from plain2code_state import RunState

LOGGER_NAME = "codeplain"


class IndentedFormatter(logging.Formatter):
    def format(self, record):
        original_message = record.getMessage()

        modified_message = original_message.replace("\n", "\n                ")

        record.msg = modified_message
        return super().format(record)


class ElapsedTimeFormatter(logging.Formatter):
    """Formatter that adds elapsed time since render started, accounting for pauses."""

    def __init__(self, run_state: RunState, fmt: str = "%(elapsed_time)s %(levelname)s %(name)s: %(message)s"):
        super().__init__(fmt=fmt)
        self.run_state = run_state

    def format(self, record):
        # Calculate elapsed time the same way as LoggingHandler does for the TUI
        try:
            offset_seconds = self.run_state.render_time_accumulated + int(
                time.monotonic() - self.run_state.last_render_start_timestamp
            )
        except Exception:
            # If RunState is not available or there's any error, default to 00:00:00
            offset_seconds = 0

        hours = offset_seconds // 3600
        minutes = (offset_seconds % 3600) // 60
        seconds = offset_seconds % 60
        elapsed_time = f"[{hours:02d}:{minutes:02d}:{seconds:02d}]"

        # Add elapsed_time to the record so it can be used in the format string
        record.elapsed_time = elapsed_time

        # Handle multi-line messages with proper indentation
        original_message = record.getMessage()
        indent = " " * len(elapsed_time + " ")
        modified_message = original_message.replace("\n", "\n" + indent)
        record.msg = modified_message

        return super().format(record)


class LoggingHandler(logging.Handler):
    def __init__(self, event_bus: EventBus, run_state: RunState):
        super().__init__()
        self.event_bus = event_bus
        self.run_state = run_state

    def emit(self, record):
        try:
            offset_seconds = self.run_state.render_time_accumulated + int(
                time.monotonic() - self.run_state.last_render_start_timestamp
            )

            hours = offset_seconds // 3600
            minutes = (offset_seconds % 3600) // 60
            seconds = offset_seconds % 60
            timestamp = f"{hours:02d}:{minutes:02d}:{seconds:02d}"

            event = LogMessageEmitted(
                logger_name=record.name,
                level=record.levelname,
                message=record.getMessage(),
                timestamp=timestamp,
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


def get_log_file_path(plain_file_path: Optional[str], log_file_name: str) -> Optional[str]:
    """Get the full path to the log file, relative to the plain file directory."""
    if not plain_file_path:
        return None
    plain_dir = os.path.dirname(os.path.abspath(plain_file_path))
    return os.path.join(plain_dir, log_file_name)


def dump_crash_logs(args, run_state: RunState, formatter=None):
    """Dump buffered logs to file if CrashLogHandler is present."""
    if args.log_to_file:
        return

    if formatter is None:
        formatter = ElapsedTimeFormatter(run_state)

    root_logger = logging.getLogger(LOGGER_NAME)
    crash_handler = next((h for h in root_logger.handlers if isinstance(h, CrashLogHandler)), None)

    if crash_handler and args.filename:
        log_file_path = get_log_file_path(args.filename, args.log_file_name)

        crash_handler.dump_to_file(log_file_path, formatter)
