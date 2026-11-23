"""Alerts REST + single-use WS tickets + /ws/events (Opus). Fan-out uses Astra's app.core.ws_bus."""
import secrets
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Query, WebSocket
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core import audit, outbox
from app.core.auth import Principal, current_principal, require_roles
from app.core.errors import ApiError
from app.core.ws_bus import bus
from app.db import models as m
from app.db.session import get_db, session_factory
from app.features.alerts.relay import AUDIENCE

router = APIRouter(tags=["alerts"])
ws_router = APIRouter()
TICKET_TTL = timedelta(seconds=60)


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
    a = db.scalars(select(m.Alert).where(m.Alert.id == alert_id).with_for_update()).first()
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
def ticket(db: Session = Depends(get_db), p: Principal = Depends(current_principal)) -> dict:
    """Bearer → short-lived single-use ticket, so the JWT never travels in a WebSocket URL."""
    t = secrets.token_urlsafe(32)
    exp = datetime.now(UTC) + TICKET_TTL
    db.add(m.WsTicket(ticket=t, user_id=p.id, expires_at=exp))
    return {"ticket": t, "expires_at": exp.isoformat().replace("+00:00", "Z")}


def _redeem(ticket: str) -> str | None:
    with session_factory()() as db:
        now = datetime.now(UTC)
        uid = db.execute(update(m.WsTicket).where(m.WsTicket.ticket == ticket, m.WsTicket.used_at.is_(None), m.WsTicket.expires_at > now)
                         .values(used_at=now).returning(m.WsTicket.user_id)).scalar_one_or_none()
        db.commit()
        if uid and not db.get(m.User, uid).active:
            return None
        return uid


@ws_router.websocket("/ws/events")
async def events(ws: WebSocket, ticket: str = ""):
    from anyio import to_thread
    uid = await to_thread.run_sync(_redeem, ticket) if ticket else None
    if not uid:
        await ws.close(code=1008)
        return
    await ws.accept()
    await bus.subscribe(ws, audience=AUDIENCE)
    try:
        while True:
            await ws.receive_text()  # clients don't send; this detects disconnects
    except Exception:
        pass
    finally:
        await bus.unsubscribe(ws)
