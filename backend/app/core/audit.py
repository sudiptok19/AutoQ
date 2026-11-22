"""Append-only, hash-chained audit log (same projection as the original mock):
hash = sha256(prev_hash + canonical({seq,at,actor,action,entity_type,entity_id,reason,data})).

Ordering rule: a transaction appends audit/outbox rows only AFTER taking all its business row locks.
`append` takes a transaction-scoped advisory lock to serialize the chain; LockSet refuses further
business locks afterwards, which rules out lock-order deadlocks between the chain and the rows."""
import hashlib
from datetime import UTC, datetime

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.canonical import canonicalize, int_safe
from app.core.ids import new_id
from app.db import models as m

GENESIS = "0" * 64
_CHAIN_LOCK = 0x41_55_44_49_54  # 'AUDIT'


def fmt_ts(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _body(seq: int, at: str, actor: str, action: str, entity_type: str, entity_id: str, reason: str | None, data: dict) -> dict:
    return {"seq": seq, "at": at, "actor": actor, "action": action, "entity_type": entity_type, "entity_id": entity_id, "reason": reason, "data": data}


def append(db: Session, actor: str, action: str, entity_type: str, entity_id: str, reason: str | None = None, data: dict | None = None) -> m.AuditEvent:
    db.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _CHAIN_LOCK})
    db.info["audit_chain_locked"] = True
    last = db.scalars(select(m.AuditEvent).order_by(m.AuditEvent.seq.desc()).limit(1)).first()
    seq = (last.seq if last else 0) + 1
    prev = last.hash if last else GENESIS
    at = datetime.now(UTC)
    clean = int_safe(data or {})
    h = hashlib.sha256((prev + canonicalize(_body(seq, fmt_ts(at), actor, action, entity_type, entity_id, reason, clean))).encode()).hexdigest()
    ev = m.AuditEvent(id=new_id("aud"), seq=seq, at=at, actor=actor, action=action, entity_type=entity_type, entity_id=entity_id, reason=reason, data=clean, prev_hash=prev, hash=h)
    db.add(ev)
    db.flush()
    return ev


def verify(db: Session) -> dict:
    prev, n = GENESIS, 0
    for e in db.scalars(select(m.AuditEvent).order_by(m.AuditEvent.seq)).yield_per(500):
        n += 1
        expect = hashlib.sha256((prev + canonicalize(_body(e.seq, fmt_ts(e.at), e.actor, e.action, e.entity_type, e.entity_id, e.reason, e.data))).encode()).hexdigest()
        if e.prev_hash != prev or e.hash != expect or e.seq != n:
            return {"ok": False, "broken_at": e.seq, "length": n}
        prev = e.hash
    return {"ok": True, "length": n, "head": prev}
