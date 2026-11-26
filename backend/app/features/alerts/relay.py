"""Outbox → WebSocket relay (Opus). Only COMMITTED outbox rows are published, and a row is marked sent only AFTER it
was handed to Astra's bus. A crash between the two re-publishes the row on restart: delivery is at-least-once, and
clients dedupe by event_id. (Marking first and publishing second, as before, could lose an event's live push.)
Clients that were disconnected catch up from the same durable rows via GET /events."""
import asyncio
import logging
from datetime import UTC, datetime

from anyio import to_thread
from sqlalchemy import select, update

from app.core.ws_bus import InvalidEvent, bus
from app.db import models as m
from app.db.session import session_factory

log = logging.getLogger("autoq.relay")
AUDIENCE = "plant"   # single-site prototype: every authenticated control-room/scanner user


def envelope(e: m.OutboxEvent) -> dict:
    return {"event_id": e.id, "type": e.type, "severity": e.severity, "audible_siren": bool(e.audible_siren), "entity_type": e.entity_type,
            "entity_id": e.entity_id, "reason_codes": list(e.reason_codes or []),
            "timestamp": e.created_at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ"), "payload": dict(e.payload or {})}


def _pending(limit: int = 100) -> list[dict]:
    """Committed, not-yet-sent rows in creation order. Read only: nothing is marked until the bus has them."""
    with session_factory()() as db:
        rows = db.scalars(select(m.OutboxEvent).where(m.OutboxEvent.sent_at.is_(None))
                          .order_by(m.OutboxEvent.created_at, m.OutboxEvent.id).limit(limit)).all()
        return [envelope(r) for r in rows]


def _mark_sent(ids: list[str]) -> None:
    if not ids:
        return
    with session_factory()() as db:
        db.execute(update(m.OutboxEvent).where(m.OutboxEvent.id.in_(ids), m.OutboxEvent.sent_at.is_(None)).values(sent_at=datetime.now(UTC)))
        db.commit()


async def relay_once() -> int:
    """Publish then mark. If publishing fails mid-batch, only the rows already handed over are marked."""
    events = await to_thread.run_sync(_pending)
    handed: list[str] = []
    try:
        for ev in events:
            try:
                await bus.publish(ev, audience=AUDIENCE)
            except InvalidEvent:
                # A row that can never pass the WS schema must not block the queue; it stays readable via GET /events.
                log.exception("outbox event %s failed the WS schema; marked sent, still available via REST", ev["event_id"])
            handed.append(ev["event_id"])
    finally:
        await to_thread.run_sync(_mark_sent, handed)
    return len(handed)


async def run_forever(interval: float = 0.5) -> None:
    while True:
        try:
            await relay_once()
        except Exception:
            log.exception("outbox relay iteration failed; retrying")
        await asyncio.sleep(interval)
