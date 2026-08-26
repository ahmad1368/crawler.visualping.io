"""FastAPI websocket endpoint streaming a crawl's progress events.

Bridges the synchronous `EventBus` (issue #16) to an async WebSocket
connection via an `asyncio.Queue`: `EventBus` handlers enqueue
synchronously (safe -- both run on the same event loop, so there's no
cross-thread `asyncio.Queue` access), and a single async loop dequeues and
sends. A `match_found` message carries a `PasswordMatch` -- the plaintext
secret + context -- to whoever is connected to this socket; treat that
connection as sensitive the same as any other sink in the pipeline.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

from app.api.routes import CrawlStatus, _crawls, app
from app.events import CRAWL_FINISHED, MATCH_FOUND, PAGE_FETCHED
from app.models import CrawlSummary, PageResult, PasswordMatch


def _serialize(event_type: str, payload: Any) -> dict:
    if isinstance(payload, (PageResult, PasswordMatch, CrawlSummary)):
        data = payload.model_dump(mode="json")
    else:
        data = payload
    return {"type": event_type, "payload": data}


@app.websocket("/ws/crawls/{crawl_id}")
async def crawl_progress_websocket(websocket: WebSocket, crawl_id: str) -> None:
    await websocket.accept()

    state = _crawls.get(crawl_id)
    if state is None:
        await websocket.close(code=4404)
        return

    if state.status is not CrawlStatus.RUNNING:
        payload = state.report if state.status is CrawlStatus.FINISHED else None
        await websocket.send_json(_serialize(CRAWL_FINISHED, payload))
        await websocket.close()
        return

    queue: asyncio.Queue = asyncio.Queue()

    def make_handler(event_type: str):
        def handler(payload: Any) -> None:
            queue.put_nowait(_serialize(event_type, payload))

        return handler

    for event_type in (PAGE_FETCHED, MATCH_FOUND, CRAWL_FINISHED):
        state.event_bus.subscribe(event_type, make_handler(event_type))

    try:
        while True:
            message = await queue.get()
            await websocket.send_json(message)
            if message["type"] == CRAWL_FINISHED:
                break
    except WebSocketDisconnect:
        return

    await websocket.close()
