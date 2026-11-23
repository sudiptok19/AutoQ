"""Outbox → WebSocket relay (Opus). Only COMMITTED outbox rows are published; each row is marked sent
after delivery to Astra's bus. Clients dedupe by event_id and catch up via GET /alerts."""
import asyncio
import logging
from datetime import UTC, datetime

from anyio import to_thread
from sqlalchemy import select

from app.core.ws_bus import InvalidEvent, bus
from app.db import models as m
from app.db.session import session_factory

log = logging.getLogger("autoq.relay")
AUDIENCE = "plant"   # single-site prototype: every authenticated control-room/scanner user


def envelope(e: m.OutboxEvent) -> dict:
    return {"event_id": e.id, "type": e.type, "severity": e.severity, "audible_siren": bool(e.audible_siren), "entity_type": e.entity_type,
            "entity_id": e.entity_id, "reason_codes": list(e.reason_codes or []),
            "timestamp": e.created_at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ"), "payload": dict(e.payload or {})}


def _claim(limit: int = 100) -> list[dict]:
    with session_factory()() as db:
        rows = db.scalars(select(m.OutboxEvent).where(m.OutboxEvent.sent_at.is_(None)).order_by(m.OutboxEvent.created_at, m.OutboxEvent.id)
                          .limit(limit).with_for_update(skip_locked=True)).all()
        out = [envelope(r) for r in rows]
        now = datetime.now(UTC)
        for r in rows:
            r.sent_at = now
        db.commit()
        return out


async def relay_once() -> int:
    events = await to_thread.run_sync(_claim)
    for ev in events:
        try:
            await bus.publish(ev, audience=AUDIENCE)
        except InvalidEvent:
            log.exception("outbox event %s failed the WS schema; left marked sent, still available via REST", ev["event_id"])
    return len(events)


async def run_forever(interval: float = 0.5) -> None:
    while True:
        try:
            await relay_once()
        except Exception:
            log.exception("outbox relay iteration failed; retrying")
        await asyncio.sleep(interval)
