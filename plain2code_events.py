from dataclasses import dataclass
from typing import Optional

from render_machine.render_types import (
    ConformanceTestsRunningContext,
    FridContext,
    ScriptExecutionHistory,
    UnitTestsRunningContext,
)


class BaseEvent:
    """Base class for all events."""

    pass


@dataclass
class RenderContextSnapshot:
    frid_context: Optional[FridContext]
    conformance_tests_running_context: Optional[ConformanceTestsRunningContext]
    unit_tests_running_context: Optional[UnitTestsRunningContext]
    script_execution_history: Optional[ScriptExecutionHistory]
    module_name: str


@dataclass
class RenderCompleted(BaseEvent):
    """Event emitted when rendering completes successfully."""

    rendered_code_path: str


@dataclass
class RenderFailed(BaseEvent):
    """Event emitted when rendering fails."""

    error_message: str


@dataclass
class LogMessageEmitted(BaseEvent):
    logger_name: str  # e.g., "codeplain"
    level: str  # e.g., "INFO", "DEBUG", "ERROR"
    message: str  # The actual log message
    timestamp: str  # Formatted timestamp
    log_color: Optional[str] = None  # Color for the message (e.g. "#FFB454"); the file log stays plain text


@dataclass
class RenderStateUpdated(BaseEvent):
    state: str
    previous_state: Optional[str]
    snapshot: RenderContextSnapshot


@dataclass
class RenderModuleCompleted(BaseEvent):
    module_name: str


@dataclass
class RenderModuleFailed(BaseEvent):
    module_name: str


@dataclass
class RenderModuleStarted(BaseEvent):
    module_name: str


@dataclass
class RenderPaused(BaseEvent):
    pass
