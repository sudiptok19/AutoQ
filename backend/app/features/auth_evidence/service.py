from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import audit
from app.core.auth import check_password, hash_password, issue_token, load_principal
from app.core.errors import ApiError
from app.core.storage import put_evidence, sniff
from app.db import models as m

_DUMMY = hash_password('not-a-real-password')


def login(db: Session, username: str, password: str) -> dict:
    u = db.scalars(select(m.User).where(m.User.username == username)).first()
    # Same error and similar timing for unknown user and bad password.
    ok = check_password(u.password_hash if u else _DUMMY, password)
    if not u or not u.active or not ok:
        raise ApiError(401, "INVALID_CREDENTIALS", "Unknown user or wrong password")
    p = load_principal(db, u.id)
    audit.append(db, u.username, "LOGIN", "user", u.id)
    return {"access_token": issue_token(u.id), "token_type": "bearer", "user": p.public()}


def list_audit(db: Session, entity_id: str | None, limit: int, before_seq: int | None) -> dict:
    q = select(m.AuditEvent).order_by(m.AuditEvent.seq.desc()).limit(limit + 1)
    if entity_id:
        q = q.where(m.AuditEvent.entity_id == entity_id)
    if before_seq:
        q = q.where(m.AuditEvent.seq < before_seq)
    rows = list(db.scalars(q))
    more = len(rows) > limit
    rows = rows[:limit]
    items = [{"id": e.id, "seq": e.seq, "at": audit.fmt_ts(e.at), "actor": e.actor, "action": e.action, "entity_type": e.entity_type, "entity_id": e.entity_id, "reason": e.reason, "data": e.data, "prev_hash": e.prev_hash, "hash": e.hash} for e in rows]
    return {"items": items, "next_cursor": str(rows[-1].seq) if more and rows else None}


UPLOAD_PURPOSES = {"REVIEW_RECORD", "SUPPLIER_DOCUMENT", "OTHER"}


def upload_evidence(db: Session, p, data: bytes, filename: str, purpose: str) -> dict:
    """Store a human-supplied record (review minutes, supplier letter) as UPLOAD evidence. The digest is what a
    report approval cites; the file itself is never interpreted."""
    if purpose not in UPLOAD_PURPOSES:
        raise ApiError(422, "VALIDATION", f"purpose must be one of {sorted(UPLOAD_PURPOSES)}")
    media = sniff(data) or "application/octet-stream"
    sha = put_evidence(db, data, media, "UPLOAD", p.id)
    audit.append(db, p.username, "EVIDENCE_UPLOADED", "evidence", sha, data={"purpose": purpose, "filename": filename[:120], "media_type": media, "bytes": len(data)})
    return {"sha256": sha, "media_type": media, "byte_size": len(data), "url": f"/api/v1/evidence/{sha}", "evidence_source": "UPLOAD", "purpose": purpose}
