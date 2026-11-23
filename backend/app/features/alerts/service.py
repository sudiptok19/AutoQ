"""Durable alerts + outbox (Opus). REST routes and the WS relay come with the alerts feature."""
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core import outbox
from app.core.ids import new_id
from app.db import models as m


def raise_alert(db: Session, *, type: str, severity: str, entity_type: str, entity_id: str, reason_codes: list[str],
                details: dict, dedupe_key: str, audible: bool = False, event_type: str = "ALERT_CREATED") -> str | None:
    """Idempotent on dedupe_key: a retry never creates a second alert or event. Returns the new id or None."""
    aid = new_id("alert")
    res = db.execute(insert(m.Alert).values(id=aid, type=type, severity=severity, audible_siren=audible, entity_type=entity_type, entity_id=entity_id,
                                            reason_codes=reason_codes, details=details, dedupe_key=dedupe_key)
                     .on_conflict_do_nothing(index_elements=["dedupe_key"]).returning(m.Alert.id))
    if res.scalar_one_or_none() is None:
        return None
    outbox.emit(db, event_type, entity_type, entity_id, severity=severity, audible_siren=audible, reason_codes=reason_codes, payload={"alert_id": aid, **details})
    return aid
