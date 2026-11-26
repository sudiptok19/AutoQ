"""P2 WebSocket reliability: publish-before-mark relay, sockets that lose their authorization, durable catch-up."""
import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update
from starlette.websockets import WebSocketDisconnect

from app.core import outbox
from app.db import models as m
from app.db.session import session_factory
from app.features.alerts import relay
from app.features.alerts.service import raise_alert

API = "/api/v1"


def emit(n=3, severity="HIGH"):
    with session_factory()() as db:
        ids = [outbox.emit(db, "ALERT_CREATED", "PACKAGE", f"pkg_{i}", severity=severity, payload={"i": i}).id for i in range(n)]
        db.commit()
        return ids


def unsent():
    with session_factory()() as db:
        return set(db.scalars(select(m.OutboxEvent.id).where(m.OutboxEvent.sent_at.is_(None))))


class FakeBus:
    def __init__(self, fail_on=None):
        self.published, self.fail_on = [], fail_on

    async def publish(self, ev, *, audience):
        if ev["event_id"] == self.fail_on:
            raise RuntimeError("bus went away")
        self.published.append(ev["event_id"])
        return 1


# ── relay: mark only what the bus received ─────────────────────────────────────────────────────────────────────
def test_publish_failure_mid_batch_marks_only_what_was_handed_over(monkeypatch):
    emit()
    a, b, c = [e["event_id"] for e in relay._pending()]          # the relay's own order (created_at, id)
    monkeypatch.setattr(relay, "bus", FakeBus(fail_on=b))
    with pytest.raises(RuntimeError):
        asyncio.run(relay.relay_once())
    assert unsent() == {b, c}                                   # a was published and marked; b and c were not lost
    good = FakeBus()
    monkeypatch.setattr(relay, "bus", good)
    assert asyncio.run(relay.relay_once()) == 2 and good.published == [b, c] and unsent() == set()


def test_crash_after_publish_before_mark_republishes_rather_than_loses(monkeypatch):
    [a] = emit(1)
    first = FakeBus()
    monkeypatch.setattr(relay, "bus", first)
    monkeypatch.setattr(relay, "_mark_sent", lambda ids: (_ for _ in ()).throw(RuntimeError("process killed")))
    with pytest.raises(RuntimeError):
        asyncio.run(relay.relay_once())
    assert first.published == [a] and unsent() == {a}           # delivered, but not recorded as delivered
    monkeypatch.undo()
    again = FakeBus()
    monkeypatch.setattr(relay, "bus", again)
    asyncio.run(relay.relay_once())
    assert again.published == [a] and unsent() == set()         # at-least-once: clients dedupe by event_id


# ── sockets lose their authorization ───────────────────────────────────────────────────────────────────────────
@pytest.fixture
def fast_reauth(monkeypatch):
    monkeypatch.setenv("AUTOQ_WS_REAUTH_SECONDS", "0.2")


def ticket(client, auth, who="op.ravi"):
    return client.post(f"{API}/ws/tickets", headers=auth(who)).json()["ticket"]


def closed_reason(ws) -> tuple[int, str]:
    with pytest.raises(WebSocketDisconnect) as e:
        while True:
            ws.receive_json()
    return e.value.code, e.value.reason


def test_deactivated_account_is_disconnected(client, auth, db, fast_reauth):
    t = ticket(client, auth)
    with client.websocket_connect(f"/ws/events?ticket={t}") as ws:
        db.execute(update(m.User).where(m.User.username == "op.ravi").values(active=False)); db.commit()
        assert closed_reason(ws) == (1008, "ACCOUNT_INACTIVE")


def test_role_change_forces_a_fresh_ticket(client, auth, db, fast_reauth):
    t = ticket(client, auth)
    with client.websocket_connect(f"/ws/events?ticket={t}") as ws:
        uid = db.scalar(select(m.User.id).where(m.User.username == "op.ravi"))
        db.add(m.UserRole(user_id=uid, role="dispatcher")); db.commit()
        assert closed_reason(ws) == (1008, "ROLES_CHANGED")


def test_socket_never_outlives_its_session(client, auth, db, fast_reauth):
    t = ticket(client, auth)
    soon = datetime.now(UTC) + timedelta(milliseconds=600)
    db.execute(update(m.WsTicket).where(m.WsTicket.ticket == t).values(session_expires_at=soon)); db.commit()
    with client.websocket_connect(f"/ws/events?ticket={t}") as ws:
        assert closed_reason(ws) == (1008, "SESSION_EXPIRED")
    stale = ticket(client, auth)
    db.execute(update(m.WsTicket).where(m.WsTicket.ticket == stale).values(session_expires_at=datetime.now(UTC) - timedelta(seconds=1))); db.commit()
    with pytest.raises(WebSocketDisconnect) as e:
        with client.websocket_connect(f"/ws/events?ticket={stale}"):
            pass
    assert e.value.code == 1008


def test_ticket_records_the_bearer_session_expiry(client, auth, db):
    t = ticket(client, auth)
    row = db.get(m.WsTicket, t)
    assert row.session_expires_at is not None and row.session_expires_at > datetime.now(UTC) + timedelta(hours=1)


# ── durable catch-up ───────────────────────────────────────────────────────────────────────────────────────────
def test_catch_up_returns_missed_critical_events_from_the_outbox(client, auth, db):
    since = (datetime.now(UTC) - timedelta(seconds=5)).isoformat()
    with session_factory()() as s:
        raise_alert(s, type="SEAL_TAMPER", severity="CRITICAL", entity_type="PACKAGE", entity_id="pkg_x", reason_codes=["SEAL_SUSPICIOUS"],
                    details={"package": "PKG-1"}, dedupe_key="k1", audible=True)
        outbox.emit(s, "DISPATCH_STATE_CHANGED", "SHIPMENT", "s1", severity="INFO")                     # routine: not caught up
        outbox.emit(s, "SEGREGATION_TASK_CREATED", "PACKAGE", "p1", severity="MEDIUM", audible_siren=True)  # loud: caught up
        s.commit()
    r = client.get(f"{API}/events", params={"since": since}, headers=auth("op.ravi")).json()
    # Same transaction → same created_at; order within it follows the (random-suffixed) id, so compare as a set.
    assert sorted(e["type"] for e in r["items"]) == ["ALERT_CREATED", "SEGREGATION_TASK_CREATED"] and r["truncated"] is False
    alert = next(e for e in r["items"] if e["type"] == "ALERT_CREATED")
    assert alert["payload"]["alert_type"] == "SEAL_TAMPER" and alert["timestamp"].endswith("Z")
    # The same rows the socket sends, even if their live push never happened (sent_at is irrelevant here).
    assert db.get(m.OutboxEvent, alert["event_id"]) is not None


def test_catch_up_is_bounded_and_strict_about_time(client, auth):
    emit(3, severity="CRITICAL")
    since = (datetime.now(UTC) - timedelta(seconds=30)).isoformat()
    r = client.get(f"{API}/events", params={"since": since, "limit": 2}, headers=auth("op.ravi")).json()
    assert len(r["items"]) == 2 and r["truncated"] is True
    old = (datetime.now(UTC) - timedelta(days=3)).isoformat()
    assert client.get(f"{API}/events", params={"since": old}, headers=auth("op.ravi")).json()["truncated"] is True
    assert client.get(f"{API}/events", params={"since": "2026-11-26T10:00:00"}, headers=auth("op.ravi")).status_code == 422
    assert client.get(f"{API}/events", params={"since": since}).status_code == 401
