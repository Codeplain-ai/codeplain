from collections import defaultdict
from typing import Any, Callable, Type

from plain2code_events import BaseEvent


class EventBus:
    def __init__(self):
        self._listeners: defaultdict[Type[BaseEvent], list[Callable[[Any], None]]] = defaultdict(list)
        self._dispatch_wrapper: Callable[[Callable], None] | None = None

    def register_dispatch_wrapper(self, fn: Callable[[Callable], None]):
        """Set a wrapper for dispatching listeners (e.g., Textual's app.call_from_thread)."""
        self._dispatch_wrapper = fn

    def subscribe(self, event_type: Type[BaseEvent], listener: Callable[[Any], None]):
        """Registers a listener for a specific event type."""
        self._listeners[event_type].append(listener)

    def publish(self, event: BaseEvent):
        """Publishes an event to all registered listeners."""

        def _dispatch():
            for listener in self._listeners[type(event)]:
                listener(event)

        if self._dispatch_wrapper:
            self._dispatch_wrapper(_dispatch)
        else:
            _dispatch()
