"""Contains all state and context information we need for the rendering process."""

import time
import uuid
from typing import Optional

from event_bus import EventBus
from plain2code_events import LastRenderStartTimestampSet, RenderTimeSet


class RunState:
    """Contains information about the identifiable state of the rendering process."""

    def __init__(self, spec_filename: str, event_bus: EventBus, replay_with: Optional[str] = None):
        self.replay: bool = replay_with is not None
        self.render_succeeded: bool = False
        self.render_generated_code_path: Optional[str] = None
        self.rendered_functionalities: int = 0
        if replay_with:
            self.render_id: str = replay_with
        else:
            self.render_id: str = str(uuid.uuid4())
        self.spec_filename: str = spec_filename
        self.call_count: int = 0
        self.unittest_batch_id: int = 0
        self.frid_render_anaysis: dict[str, str] = {}
        self.render_time_accumulated: int = 0
        self.last_render_start_timestamp: float | None = time.monotonic()
        self.event_bus = event_bus

    def increment_call_count(self):
        self.call_count += 1

    def increment_unittest_batch_id(self):
        self.unittest_batch_id += 1

    def add_rendering_analysis_for_frid(self, frid, rendering_analysis) -> None:
        self.frid_render_anaysis[frid] = rendering_analysis

    def set_render_succeeded(self, succeeded: bool):
        self.render_succeeded = succeeded

    def set_render_generated_code_path(self, generated_code_path: str):
        self.render_generated_code_path = generated_code_path

    def increment_rendered_functionalities(self):
        self.rendered_functionalities += 1

    def add_to_render_time(self):
        self.render_time_accumulated += int(time.monotonic() - self.last_render_start_timestamp)
        self.event_bus.publish(RenderTimeSet(render_time_accumulated=self.render_time_accumulated))

    def set_last_render_start_timestamp(self, finished_rendering: bool = False):
        self.last_render_start_timestamp = time.monotonic() if finished_rendering == False else None
        self.event_bus.publish(
            LastRenderStartTimestampSet(last_render_start_timestamp=self.last_render_start_timestamp)
        )

    def to_dict(self):
        return {
            "render_id": self.render_id,
            "call_count": self.call_count,
            "replay": self.replay,
            "spec_filename": self.spec_filename,
        }

    def get_render_func_id(self, frid: str) -> str:
        return f"{self.render_id}-{frid}"
