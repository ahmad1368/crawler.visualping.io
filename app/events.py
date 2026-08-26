"""In-process publish/subscribe event bus (Observer pattern).

Lets the orchestrator emit progress events (`page_fetched`, `match_found`,
`crawl_finished`) without depending on the web/API layer -- any number of
subscribers (e.g. a future WebSocket broadcaster) can listen without the
publisher knowing they exist. `match_found` payloads carry a
`PasswordMatch`, so anything subscribing to it inherits the same
sensitive-data handling responsibility as the rest of the pipeline.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from typing import Any

PAGE_FETCHED = "page_fetched"
MATCH_FOUND = "match_found"
CRAWL_FINISHED = "crawl_finished"

EventHandler = Callable[[Any], None]


class EventBus:
    def __init__(self) -> None:
        self._subscribers: dict[str, list[EventHandler]] = defaultdict(list)

    def subscribe(self, event_type: str, handler: EventHandler) -> None:
        self._subscribers[event_type].append(handler)

    def publish(self, event_type: str, payload: Any = None) -> None:
        for handler in self._subscribers.get(event_type, []):
            handler(payload)
