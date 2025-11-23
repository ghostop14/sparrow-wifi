from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any, Callable, Dict, List

EventHandler = Callable[[Dict[str, Any]], None]


class EventBus:
    def __init__(self) -> None:
        self._subscribers: Dict[str, List[EventHandler]] = defaultdict(list)

    def subscribe(self, event_name: str, handler: EventHandler) -> None:
        self._subscribers[event_name].append(handler)

    def publish(self, event_name: str, payload: Dict[str, Any]) -> None:
        for handler in list(self._subscribers.get(event_name, [])):
            try:
                handler(payload)
            except Exception as exc:  # pylint: disable=broad-except
                try:
                    loop = asyncio.get_running_loop()
                    loop.call_soon(handler, payload)
                except RuntimeError:
                    print(f"Event handler error for {event_name}: {exc}")


event_bus = EventBus()
