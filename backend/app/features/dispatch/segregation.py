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
          "DEVICE_RECONCILIATION": "Physically reconcile the truck and staging lane against the manifest",
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
    """Record the physical action, without ever deleting a load that some other legitimate scan created.

    REMOVAL_REQUIRED (recall / departure re-validation): the recorded load IS the problem, so completing it unloads —
    but only a load on the shipment the task names.
    SEGREGATION_REQUIRED (an offline load the server refused): that event never created a load, so any active load on
    the package belongs to a different, accepted scan (same truck or another). It is never removed here.
    Either way, a package recorded as loaded where the task does not expect it is a conflicting physical location:
    a Quality Engineer/Manager must verify where the crate really is and record the resolution; the load is unchanged."""
    t0 = db.get(m.SegregationTask, task_id)
    if not t0:
        raise ApiError(404, "TASK_NOT_FOUND", "Segregation task not found")
    locks = lock_set(db)
    pkg = None
    if t0.package_id:
        locks.lock(m.Unit, list(db.scalars(select(m.PackageItem.unit_id).where(m.PackageItem.package_id == t0.package_id, m.PackageItem.removed_at.is_(None)))))
        [pkg] = locks.lock(m.Package, [t0.package_id])
    # With the package locked no load can appear or move; lock every shipment involved, in id order.
    load = db.scalars(select(m.Load).where(m.Load.package_id == pkg.id, m.Load.removed_at.is_(None))).first() if pkg else None
    locks.lock(m.Shipment, [x for x in {t0.shipment_id, load.shipment_id if load else None} if x])
    t = db.scalars(select(m.SegregationTask).where(m.SegregationTask.id == task_id).with_for_update().execution_options(populate_existing=True)).one()
    if t.status != "OPEN":
        raise ApiError(409, "TASK_COMPLETED", "Task already completed")
    now = datetime.now(UTC)
    unloaded, elsewhere, reopened = False, None, None
    if t.type in ("REMOVAL_REQUIRED", "SEGREGATION_REQUIRED") and load is not None:
        if t.type == "REMOVAL_REQUIRED" and (t.shipment_id is None or load.shipment_id == t.shipment_id):
            ship = db.get(m.Shipment, load.shipment_id)
            if ship.status in ("IN_TRANSIT", "DELIVERED"):
                raise ApiError(409, "PACKAGE_DEPARTED", "Package already departed; use an in-transit recall task")
            load.removed_at, load.removed_reason = now, f"{t.type}: {reason}"
            pkg.state = "SEALED"
            unloaded = True
            if ship.status == "CLOSED":
                # The closed manifest no longer describes the truck: reopen it, so it must pass every close check again.
                ship.status, ship.closed_at = "OPEN", None
                reopened = ship
        else:
            elsewhere = db.get(m.Shipment, load.shipment_id)
            if not p.has("quality_engineer", "quality_manager"):
                raise ApiError(409, "LOCATION_CONFLICT_REVIEW", f"{pkg.label} is recorded as loaded on {elsewhere.code} by another accepted scan. "
                               "A quality engineer must verify where the crate physically is; that load is not changed by this task.",
                               {"package": pkg.label, "recorded_on": elsewhere.code, "task_shipment_id": t.shipment_id})
    if t.type == "DEVICE_RECONCILIATION" and not p.has("quality_engineer", "quality_manager"):
        raise ApiError(403, "ROLE_FORBIDDEN", "A lost device's physical reconciliation is signed off by a quality engineer or manager")
    t.status, t.completed_by, t.completed_at, t.completion_reason = "COMPLETED", p.id, now, reason
    if t.type == "DEVICE_RECONCILIATION":
        from app.features.offline_scanner.service import complete_recovery   # (circular at import time)
        complete_recovery(db, p, t, now)
    db.flush()
    data = {"type": t.type, "unloaded": unloaded}
    if elsewhere:
        data |= {"location_conflict": True, "recorded_on": elsewhere.code, "load_left_unchanged": True}
    audit.append(db, p.username, "SEGREGATION_LOCATION_CONFLICT_RESOLVED" if elsewhere else "SEGREGATION_TASK_COMPLETED", "segregation_task", t.id, reason, data=data)
    if unloaded:
        outbox.emit(db, "DISPATCH_STATE_CHANGED", "PACKAGE", pkg.id, severity="MEDIUM", reason_codes=[t.type], payload={"state": pkg.state, "unloaded": True})
    if reopened:
        audit.append(db, p.username, "MANIFEST_REOPENED", "shipment", reopened.id, f"{pkg.label} removed by task {t.id}", data={"task": t.id, "package": pkg.label})
        outbox.emit(db, "DISPATCH_STATE_CHANGED", "SHIPMENT", reopened.id, severity="MEDIUM", reason_codes=["MANIFEST_REOPENED"], payload={"status": "OPEN"})
    out = view(db, t)
    if elsewhere:
        out["location_conflict"] = {"recorded_on": elsewhere.code, "load_left_unchanged": True}
    return out
