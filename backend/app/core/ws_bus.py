"""In-process WebSocket delivery for events from the committed outbox."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from fastapi import WebSocket, WebSocketDisconnect
from jsonschema import Draft202012Validator, FormatChecker, ValidationError
from starlette.websockets import WebSocketState

_SCHEMA_PATH = Path(__file__).resolve().parents[3] / "contracts" / "ws_events.schema.json"
_SCHEMA = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
_FORMATS = FormatChecker()
_CLOSE_TIMEOUT_SECONDS = 5
_log = logging.getLogger(__name__)


@_FORMATS.checks("date-time", raises=ValueError)
def _utc_datetime(value: object) -> bool:
    if not isinstance(value, str):
        return True  # The schema checks the instance type.
    return value.endswith("Z") and datetime.fromisoformat(value).utcoffset() is not None


Draft202012Validator.check_schema(_SCHEMA)
_VALIDATOR = Draft202012Validator(_SCHEMA, format_checker=_FORMATS)


class InvalidEvent(ValueError):
    """The event cannot be delivered under the WebSocket wire contract."""

    error_code = "INVALID_EVENT"
    component = "ws_bus"
    remediation = "Check the event against contracts/ws_events.schema.json."

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def _event_text(event: Mapping) -> str:
    if not isinstance(event, Mapping) or any(not isinstance(key, str) for key in event):
        raise InvalidEvent("WebSocket event must be an object with string field names")
    body = dict(event)
    try:
        _VALIDATOR.validate(body)
        text = json.dumps(body, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        text.encode("utf-8")
    except ValidationError as exc:
        location = ".".join(map(str, exc.absolute_path)) or "event"
        raise InvalidEvent(f"WebSocket {location} failed the {exc.validator} schema rule") from None
    except (TypeError, ValueError, RecursionError) as exc:
        raise InvalidEvent("WebSocket event cannot be serialized as UTF-8 JSON") from exc
    return text


@dataclass
class _Subscription:
    audience: str
    queue: asyncio.Queue[str]
    writer: asyncio.Task[None]
    cleanup: asyncio.Task[None] | None = None


class Bus:
    """Bounded fan-out on one event loop. The authenticated route accepts and reads sockets."""

    def __init__(self, queue_size: int = 100):
        if type(queue_size) is not int or queue_size < 1:
            raise ValueError("WebSocket queue size must be a positive integer")
        self._queue_size = queue_size
        self._subscriptions: dict[WebSocket, _Subscription] = {}

    async def subscribe(self, socket: WebSocket, *, audience: str) -> None:
        """Start delivering to an accepted socket with a server-derived audience."""
        if not isinstance(audience, str) or not audience:
            raise ValueError("A server-derived audience string is required")
        existing = self._subscriptions.get(socket)
        if existing is not None:
            if existing.audience != audience:
                raise ValueError("A subscribed WebSocket cannot change audiences")
            return
        if socket.application_state != WebSocketState.CONNECTED or socket.client_state == WebSocketState.DISCONNECTED:
            raise ValueError("WebSocket must be accepted before subscribing")

        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=self._queue_size)
        writer = asyncio.create_task(self._write(socket, queue))
        self._subscriptions[socket] = _Subscription(audience, queue, writer)

    async def unsubscribe(self, socket: WebSocket) -> None:
        """Stop delivery and wait for any already-started writer cleanup."""
        subscription = self._subscriptions.get(socket)
        if subscription is not None:
            cleanup = self._retire(socket, subscription)
            await asyncio.shield(cleanup)

    async def publish(self, event: Mapping, *, audience: str) -> int:
        """Validate an event and queue immutable wire data for the selected audience."""
        if not isinstance(audience, str) or not audience:
            raise ValueError("A server-derived audience string is required")
        text = _event_text(event)
        queued = 0
        for socket, subscription in tuple(self._subscriptions.items()):
            if subscription.audience != audience or subscription.cleanup is not None:
                continue
            try:
                subscription.queue.put_nowait(text)
            except asyncio.QueueFull:
                self._retire(socket, subscription, close_code=1013)
            else:
                queued += 1
        return queued

    def _retire(
        self,
        socket: WebSocket,
        subscription: _Subscription,
        *,
        close_code: int | None = None,
    ) -> asyncio.Task[None]:
        if subscription.cleanup is None:
            subscription.writer.cancel()
            subscription.cleanup = asyncio.create_task(self._finish(socket, subscription, close_code))
        return subscription.cleanup

    async def _write(self, socket: WebSocket, queue: asyncio.Queue[str]) -> None:
        try:
            while True:
                await socket.send_text(await queue.get())
        except (WebSocketDisconnect, OSError, RuntimeError):
            pass  # The transport is already closing or disconnected.
        except Exception:
            _log.exception("WebSocket event writer failed")
        finally:
            subscription = self._subscriptions.get(socket)
            if subscription is not None and subscription.cleanup is None:
                self._subscriptions.pop(socket)

    async def _finish(self, socket: WebSocket, subscription: _Subscription, close_code: int | None) -> None:
        try:
            with suppress(asyncio.CancelledError):
                await subscription.writer
            if close_code is not None:
                try:
                    async with asyncio.timeout(_CLOSE_TIMEOUT_SECONDS):
                        await socket.close(code=close_code)
                except (WebSocketDisconnect, OSError, RuntimeError, TimeoutError):
                    pass  # An unavailable transport cannot receive the close frame.
        finally:
            self._subscriptions.pop(socket, None)


bus = Bus()
