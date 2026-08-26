"""ASGI entrypoint: run with `uvicorn app.main:app`.

Importing `app.api.websocket` registers its route on the shared `app`
instance from `app.api.routes` as a side effect -- it must be imported
somewhere before the server starts, or `/ws/crawls/{id}` never exists.
"""

from app.api import websocket  # noqa: F401
from app.api.routes import app

__all__ = ["app"]
