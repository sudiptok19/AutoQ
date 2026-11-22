"""Transactional outbox: events are rows written with the business fact. A relay publishes them to
the WS bus (Astra's ws_bus) only after commit; clients catch up via REST and dedupe by event_id."""
from sqlalchemy.orm import Session

from app.core.ids import new_id
from app.db import models as m

TYPES = {"ALERT_CREATED", "ALERT_RESOLVED", "PASSPORT_REVOKED", "LOT_4M_CHANGED", "DISPATCH_STATE_CHANGED", "SEGREGATION_TASK_CREATED", "SPC_DRIFT_WARNING"}


def emit(db: Session, type: str, entity_type: str, entity_id: str, *, severity: str = "INFO", audible_siren: bool = False, reason_codes: list[str] | None = None, payload: dict | None = None) -> m.OutboxEvent:
    if type not in TYPES:
        raise ValueError(f"unknown event type {type}")
    ev = m.OutboxEvent(id=new_id("evt"), type=type, severity=severity, audible_siren=audible_siren, entity_type=entity_type, entity_id=entity_id, reason_codes=reason_codes or [], payload=payload or {})
    db.add(ev)
    return ev
