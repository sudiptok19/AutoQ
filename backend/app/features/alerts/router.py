"""Alerts REST + single-use WS tickets + /ws/events + durable catch-up (Opus). Fan-out uses Astra's app.core.ws_bus.

A socket stays authorized only as long as its user does: it is re-checked every AUTOQ_WS_REAUTH_SECONDS (default 15)
and closed (1008) when the account is deactivated, its roles change, or the session that minted the ticket expires.
The client then reconnects with a fresh ticket, which re-derives everything, or is signed out if it no longer can."""
import asyncio
import os
import secrets
from datetime import UTC, datetime, timedelta

from anyio import to_thread
from fastapi import APIRouter, Depends, Query, Request, WebSocket
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core import audit, outbox
from app.core.auth import Principal, current_principal, load_principal, require_roles, session_expiry
from app.core.errors import ApiError
from app.core.ws_bus import bus
from app.db import models as m
from app.db.session import get_db, session_factory
from app.features.alerts.relay import AUDIENCE, envelope

router = APIRouter(tags=["alerts"])
ws_router = APIRouter()
TICKET_TTL = timedelta(seconds=60)
CATCH_UP_WINDOW = timedelta(hours=24)


def reauth_seconds() -> float:
    return float(os.environ.get("AUTOQ_WS_REAUTH_SECONDS", "15"))


def view(a: m.Alert) -> dict:
    iso = lambda d: d.astimezone(UTC).isoformat().replace("+00:00", "Z") if d else None  # noqa: E731
    return {"id": a.id, "type": a.type, "severity": a.severity, "audible_siren": a.audible_siren, "entity_type": a.entity_type, "entity_id": a.entity_id,
            "reason_codes": a.reason_codes, "details": a.details, "status": a.status, "created_at": iso(a.created_at),
            "ack": {"by": a.ack_by, "at": iso(a.ack_at), "reason": a.ack_reason} if a.ack_at else None,
            "resolution": {"by": a.resolved_by, "at": iso(a.resolved_at), "reason": a.resolution_reason} if a.resolved_at else None}


@router.get("/alerts")
def list_alerts(status: str | None = Query(None, pattern="^(OPEN|ACK|RESOLVED)$"), limit: int = Query(100, ge=1, le=500),
                db: Session = Depends(get_db), _: Principal = Depends(current_principal)) -> dict:
    q = select(m.Alert).order_by(m.Alert.created_at.desc()).limit(limit)
    if status:
        q = q.where(m.Alert.status == status)
    return {"items": [view(a) for a in db.scalars(q)], "next_cursor": None}


class ReasonIn(BaseModel):
    reason: str = Field(min_length=1, max_length=2000)


def _transition(db: Session, alert_id: str, p: Principal, target: str, reason: str) -> dict:
    a = db.scalars(select(m.Alert).where(m.Alert.id == alert_id).with_for_update().execution_options(populate_existing=True)).first()
    if not a:
        raise ApiError(404, "ALERT_NOT_FOUND", "Alert not found")
    if a.status == "RESOLVED" or (target == "ACK" and a.status == "ACK"):
        raise ApiError(409, "ALERT_STATE", f"Alert is already {a.status}")
    now = datetime.now(UTC)
    if target == "ACK":
        a.status, a.ack_by, a.ack_at, a.ack_reason = "ACK", p.id, now, reason
    else:
        a.status, a.resolved_by, a.resolved_at, a.resolution_reason = "RESOLVED", p.id, now, reason
        outbox.emit(db, "ALERT_RESOLVED", a.entity_type, a.entity_id, severity="INFO", reason_codes=a.reason_codes, payload={"alert_id": a.id})
    db.flush()
    audit.append(db, p.username, f"ALERT_{target}", "alert", a.id, reason)
    return view(a)


@router.post("/alerts/{alert_id}/ack")
def ack(alert_id: str, body: ReasonIn, db: Session = Depends(get_db), p: Principal = Depends(current_principal)) -> dict:
    return _transition(db, alert_id, p, "ACK", body.reason)


@router.post("/alerts/{alert_id}/resolve")
def resolve(alert_id: str, body: ReasonIn, db: Session = Depends(get_db), p: Principal = Depends(require_roles("quality_engineer", "quality_manager", "dispatcher"))) -> dict:
    return _transition(db, alert_id, p, "RESOLVED", body.reason)


@router.post("/ws/tickets")
def ticket(request: Request, db: Session = Depends(get_db), p: Principal = Depends(current_principal)) -> dict:
    """Bearer → short-lived single-use ticket, so the JWT never travels in a WebSocket URL. The ticket remembers when
    that bearer session expires: the socket it opens closes then."""
    t = secrets.token_urlsafe(32)
    exp = datetime.now(UTC) + TICKET_TTL
    db.add(m.WsTicket(ticket=t, user_id=p.id, expires_at=exp, session_expires_at=session_expiry(request)))
    return {"ticket": t, "expires_at": exp.isoformat().replace("+00:00", "Z")}


SEVERITIES = {"MEDIUM": ("MEDIUM", "HIGH", "CRITICAL"), "HIGH": ("HIGH", "CRITICAL"), "CRITICAL": ("CRITICAL",)}


@router.get("/events")
def catch_up(since: datetime = Query(..., description="ISO time with timezone; overlap a little and dedupe by event_id"),
             min_severity: str = Query("HIGH", pattern="^(MEDIUM|HIGH|CRITICAL)$"), limit: int = Query(100, ge=1, le=500),
             db: Session = Depends(get_db), _: Principal = Depends(current_principal)) -> dict:
    """Durable recovery for a client that was not connected: committed outbox events (the same envelope the socket
    sends) at or after `since`, at or above `min_severity`, plus anything that sounded the siren. Served from the
    outbox itself, so an event is here even if its live push was never delivered. At most the last 24 h."""
    if since.tzinfo is None:
        raise ApiError(422, "VALIDATION", "since must carry a timezone")
    now = datetime.now(UTC)
    floor = max(since, now - CATCH_UP_WINDOW)
    rows = list(db.scalars(select(m.OutboxEvent).where(m.OutboxEvent.created_at >= floor,
                                                        (m.OutboxEvent.severity.in_(SEVERITIES[min_severity])) | m.OutboxEvent.audible_siren.is_(True))
                           .order_by(m.OutboxEvent.created_at, m.OutboxEvent.id).limit(limit + 1)))
    return {"items": [envelope(r) for r in rows[:limit]], "truncated": len(rows) > limit or since < now - CATCH_UP_WINDOW,
            "as_of": now.isoformat().replace("+00:00", "Z")}


def _redeem(ticket: str) -> tuple[str, tuple[str, ...], datetime | None] | None:
    with session_factory()() as db:
        now = datetime.now(UTC)
        row = db.execute(update(m.WsTicket).where(m.WsTicket.ticket == ticket, m.WsTicket.used_at.is_(None), m.WsTicket.expires_at > now)
                         .values(used_at=now).returning(m.WsTicket.user_id, m.WsTicket.session_expires_at)).first()
        db.commit()
        if not row:
            return None
        p = load_principal(db, row.user_id)
        if not p or not p.roles or (row.session_expires_at and row.session_expires_at <= now):
            return None
        return p.id, p.roles, row.session_expires_at


def _revoked(user_id: str, roles: tuple[str, ...]) -> str | None:
    """Why this socket's authorization no longer holds, or None."""
    with session_factory()() as db:
        p = load_principal(db, user_id)
    if not p:
        return "ACCOUNT_INACTIVE"
    if not p.roles:
        return "NO_ROLES"
    if p.roles != roles:
        return "ROLES_CHANGED"   # reconnecting re-derives the audience from the new roles
    return None


async def _guard(ws: WebSocket, user_id: str, roles: tuple[str, ...], session_expires_at: datetime | None) -> None:
    while True:
        wait = reauth_seconds()
        if session_expires_at:
            wait = min(wait, max(0.0, (session_expires_at - datetime.now(UTC)).total_seconds()))
        await asyncio.sleep(wait)
        reason = "SESSION_EXPIRED" if session_expires_at and datetime.now(UTC) >= session_expires_at else await to_thread.run_sync(_revoked, user_id, roles)
        if reason:
            await bus.unsubscribe(ws)        # stop delivery before anything else
            try:
                await ws.close(code=1008, reason=reason)
            except Exception:
                pass
            return


@ws_router.websocket("/ws/events")
async def events(ws: WebSocket, ticket: str = ""):
    got = await to_thread.run_sync(_redeem, ticket) if ticket else None
    if not got:
        await ws.close(code=1008)
        return
    user_id, roles, session_expires_at = got
    await ws.accept()
    await bus.subscribe(ws, audience=AUDIENCE)
    guard = asyncio.create_task(_guard(ws, user_id, roles, session_expires_at))
    try:
        while True:
            await ws.receive_text()  # clients don't send; this detects disconnects
    except Exception:
        pass
    finally:
        guard.cancel()
        await bus.unsubscribe(ws)
