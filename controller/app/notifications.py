from __future__ import annotations

import asyncio
from typing import Dict

from .events import event_bus
from .notifier import format_event, notifier

_event_loop: asyncio.AbstractEventLoop | None = None


def set_notification_loop(loop: asyncio.AbstractEventLoop) -> None:
    global _event_loop  # pylint: disable=global-statement
    _event_loop = loop


SUBSCRIBED_EVENTS = (
    'scan.started',
    'scan.progress',
    'scan.completed',
    'scan.failed',
    'ingest.received',
)


def setup_notifications() -> None:
    for event_name in SUBSCRIBED_EVENTS:
        event_bus.subscribe(event_name, _make_handler(event_name))


def _make_handler(event_name: str):
    def handler(payload: Dict):
        message = format_event(event_name, payload)
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(notifier.broadcast(message))
        except RuntimeError:
            if _event_loop:
                asyncio.run_coroutine_threadsafe(notifier.broadcast(message), _event_loop)
            else:
                print(f"Event handler error for {event_name}: no event loop available")

    return handler
