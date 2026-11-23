"""Durable physical-action tasks (CONTRACT §10.4). Idempotent by dedupe_key; repeated recall/replay never duplicates
an open task. Completing a REMOVAL_REQUIRED task records the unload (closes the active load)."""
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core import audit, outbox
from app.core.auth import Principal
from app.core.errors import ApiError
from app.core.ids import new_id
from app.db import models as m
from app.db.locking import lock_set

ACTION = {"REMOVAL_REQUIRED": "Physically unload and segregate the package before departure",
          "SEGREGATION_REQUIRED": "Offline-staged load rejected on sync: segregate the package",
          "IN_TRANSIT_RECALL": "Contact carrier/consignee: intercept and quarantine on arrival",
          "NOTIFY_CUSTOMER": "Notify the consignee and arrange return or containment"}


def _iso(dt):
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z") if dt else None


def create(db: Session, *, type: str, dedupe_key: str, reason: str, package_id: str | None = None, shipment_id: str | None = None,
           recall_id: str | None = None, scan_id: str | None = None, consignee_action: dict | None = None) -> str | None:
    tid = new_id("task")
    got = db.execute(insert(m.SegregationTask).values(id=tid, type=type, dedupe_key=dedupe_key, reason=reason, package_id=package_id, shipment_id=shipment_id,
                                                      recall_id=recall_id, scan_id=scan_id, consignee_action=consignee_action or {"action": ACTION[type]})
                     .on_conflict_do_nothing(index_elements=["dedupe_key"]).returning(m.SegregationTask.id)).scalar_one_or_none()
    if got:
        label = db.get(m.Package, package_id).label if package_id else None
        outbox.emit(db, "SEGREGATION_TASK_CREATED", "PACKAGE" if package_id else "SHIPMENT", package_id or shipment_id or tid,
                    severity="HIGH", audible_siren=type in ("REMOVAL_REQUIRED", "SEGREGATION_REQUIRED"), reason_codes=[type],
                    payload={"task_id": tid, "type": type, "package": label, "shipment_id": shipment_id})
    return got


def view(db: Session, t: m.SegregationTask) -> dict:
    pkg = db.get(m.Package, t.package_id) if t.package_id else None
    shp = db.get(m.Shipment, t.shipment_id) if t.shipment_id else None
    return {"id": t.id, "type": t.type, "status": t.status, "reason": t.reason, "package_id": t.package_id, "package_label": pkg.label if pkg else None,
            "shipment_id": t.shipment_id, "shipment_code": shp.code if shp else None, "recall_id": t.recall_id, "scan_id": t.scan_id,
            "consignee_action": t.consignee_action, "created_at": _iso(t.created_at),
            "completed_by": db.get(m.User, t.completed_by).username if t.completed_by else None, "completed_at": _iso(t.completed_at),
            "completion_reason": t.completion_reason}


def complete(db: Session, p: Principal, task_id: str, reason: str) -> dict:
    t0 = db.get(m.SegregationTask, task_id)
    if not t0:
        raise ApiError(404, "TASK_NOT_FOUND", "Segregation task not found")
    locks = lock_set(db)
    if t0.package_id:
        locks.lock(m.Unit, list(db.scalars(select(m.PackageItem.unit_id).where(m.PackageItem.package_id == t0.package_id, m.PackageItem.removed_at.is_(None)))))
        [pkg] = locks.lock(m.Package, [t0.package_id])
    else:
        pkg = None
    if t0.shipment_id:
        locks.lock(m.Shipment, [t0.shipment_id])
    t = db.scalars(select(m.SegregationTask).where(m.SegregationTask.id == task_id).with_for_update()).one()
    if t.status != "OPEN":
        raise ApiError(409, "TASK_COMPLETED", "Task already completed")
    now = datetime.now(UTC)
    unloaded = False
    if t.type in ("REMOVAL_REQUIRED", "SEGREGATION_REQUIRED") and pkg is not None:
        load = db.scalars(select(m.Load).where(m.Load.package_id == pkg.id, m.Load.removed_at.is_(None))).first()
        if load:
            ship = db.get(m.Shipment, load.shipment_id)
            if ship.status in ("IN_TRANSIT", "DELIVERED"):
                raise ApiError(409, "PACKAGE_DEPARTED", "Package already departed; use an in-transit recall task")
            load.removed_at, load.removed_reason = now, f"{t.type}: {reason}"
            pkg.state = "SEALED"
            unloaded = True
    t.status, t.completed_by, t.completed_at, t.completion_reason = "COMPLETED", p.id, now, reason
    db.flush()
    audit.append(db, p.username, "SEGREGATION_TASK_COMPLETED", "segregation_task", t.id, reason, data={"type": t.type, "unloaded": unloaded})
    if unloaded:
        outbox.emit(db, "DISPATCH_STATE_CHANGED", "PACKAGE", pkg.id, severity="MEDIUM", reason_codes=[t.type], payload={"state": pkg.state, "unloaded": True})
    return view(db, t)
