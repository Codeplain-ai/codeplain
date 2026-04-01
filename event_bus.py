from collections import defaultdict
from typing import Any, Callable, Type

from plain2code_events import BaseEvent


class EventBus:
    def __init__(self):
        self._listeners: defaultdict[Type[BaseEvent], list[Callable[[Any], None]]] = defaultdict(list)

    def subscribe(self, event_type: Type[BaseEvent], listener: Callable[[Any], None]):
        """Registers a listener for a specific event type."""
        self._listeners[event_type].append(listener)

    def publish(self, event: BaseEvent):
        """Publishes an event to all registered listeners."""
        for listener in self._listeners[type(event)]:
            listener(event)
