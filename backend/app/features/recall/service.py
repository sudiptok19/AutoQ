"""Genealogy trace, recall blast radius, atomic quarantine and release (CONTRACT §8.3, §8.4, §10.4). Owner: Opus.

Genealogy edges: supplier → heat → lot_heats → lot → unit → package_item (open) → package → active load → shipment
→ customer. Physical location comes ONLY from accepted loads + shipment movement state, never from a flag:
  at_plant (no active load) · loaded_not_departed (shipment OPEN/CLOSED) · in_transit · delivered.
Each affected unit is counted exactly once. Every whole package containing an affected unit is held once (clean units
in a mixed package are held physically but keep their own clean lineage). Quarantine runs in one transaction under the
global lock order heat → lot → unit → package → order → shipment; departure racing it either commits first (→ an
IN_TRANSIT_RECALL task) or is blocked (→ REMOVAL_REQUIRED).
"""
import time
from collections import defaultdict
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import audit, outbox
from app.core.auth import Principal
from app.core.errors import ApiError
from app.core.ids import new_id
from app.db import models as m
from app.db.locking import lock_set
from app.features.alerts.service import raise_alert
from app.features.dispatch import segregation
from app.policy import eligibility

QUERY = "heats -> lot_heats -> lots -> units -> package_items(open) -> packages -> loads(active) -> shipments -> movement state"
BUCKET = {None: "at_plant", "OPEN": "loaded_not_departed", "CLOSED": "loaded_not_departed", "IN_TRANSIT": "in_transit", "DELIVERED": "delivered"}


def _iso(dt):
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z") if dt else None


# ── scope resolution ───────────────────────────────────────────────────────
def resolve(db: Session, type_: str | None, key: str) -> tuple[str, str, str]:
    """(scope_type, entity_id, human label). Accepts ids or human codes (heat code, lot code, serial, package label)."""
    finders = [("HEAT", m.Heat, m.Heat.heat_code, "heat_code"), ("LOT", m.Lot, m.Lot.lot_code, "lot_code"),
               ("PACKAGE", m.Package, m.Package.label, "label"), ("UNIT", m.Unit, m.Unit.serial, "serial")]
    for t, cls, col, attr in finders:
        if type_ and t != type_:
            continue
        row = db.get(cls, key) or db.scalars(select(cls).where(col == key)).first()
        if row:
            return t, row.id, getattr(row, attr)
    pp = db.get(m.Passport, key) if not type_ or type_ == "UNIT" else None
    if pp:
        u = db.get(m.Unit, pp.unit_id)
        return "UNIT", u.id, u.serial
    raise ApiError(404, "SCOPE_NOT_FOUND", f"Nothing matches {key!r}", {"type": type_})


def unit_ids_in_scope(db: Session, scope_type: str, entity_id: str) -> list[str]:
    if scope_type == "HEAT":
        q = select(m.Unit.id).join(m.LotHeat, m.LotHeat.lot_id == m.Unit.lot_id).where(m.LotHeat.heat_id == entity_id)
    elif scope_type == "LOT":
        q = select(m.Unit.id).where(m.Unit.lot_id == entity_id)
    elif scope_type == "PACKAGE":
        q = select(m.PackageItem.unit_id).where(m.PackageItem.package_id == entity_id, m.PackageItem.removed_at.is_(None))
    else:
        q = select(m.Unit.id).where(m.Unit.id == entity_id)
    return sorted(set(db.scalars(q)))


def placements(db: Session, unit_ids: list[str]) -> dict[str, tuple[str | None, str | None, str | None]]:
    """unit → (package_id, shipment_id, shipment_status) from open membership + active load."""
    rows = db.execute(select(m.PackageItem.unit_id, m.PackageItem.package_id, m.Load.shipment_id, m.Shipment.status)
                      .select_from(m.PackageItem)
                      .join(m.Load, (m.Load.package_id == m.PackageItem.package_id) & m.Load.removed_at.is_(None), isouter=True)
                      .join(m.Shipment, m.Shipment.id == m.Load.shipment_id, isouter=True)
                      .where(m.PackageItem.unit_id.in_(unit_ids), m.PackageItem.removed_at.is_(None)))
    out = {u: (None, None, None) for u in unit_ids}
    for uid, pkg, shp, st in rows:
        out[uid] = (pkg, shp, st)
    return out


def impact(db: Session, scope_type: str, entity_id: str, label: str) -> dict:
    t0 = time.perf_counter()
    ids = unit_ids_in_scope(db, scope_type, entity_id)
    place = placements(db, ids)
    counts = {"at_plant": 0, "loaded_not_departed": 0, "in_transit": 0, "delivered": 0}
    pkg_units: dict[str, int] = defaultdict(int)
    ship_units: dict[str, int] = defaultdict(int)
    for uid in ids:
        pkg, shp, st = place[uid]
        counts[BUCKET[st]] += 1
        if pkg:
            pkg_units[pkg] += 1
        if shp:
            ship_units[shp] += 1
    held_pkgs, at_plant, loaded, transit, delivered = [], [], [], [], []
    for pid, n in sorted(pkg_units.items()):
        pkg = db.get(m.Package, pid)
        total = len(list(db.scalars(select(m.PackageItem.id).where(m.PackageItem.package_id == pid, m.PackageItem.removed_at.is_(None)))))
        _, shp, st = next(place[u] for u in ids if place[u][0] == pid)
        entry = {"package_id": pid, "label": pkg.label, "units": n, "package_units": total, "mixed": total > n}
        if st in (None,):
            held_pkgs.append(pid); at_plant.append(entry)
        elif st in ("OPEN", "CLOSED"):
            held_pkgs.append(pid); loaded.append(entry | {"shipment_id": shp, "code": db.get(m.Shipment, shp).code, "action": "REMOVAL_REQUIRED"})
    for sid, n in sorted(ship_units.items()):
        s = db.get(m.Shipment, sid)
        o = db.get(m.Order, s.order_id)
        cus = db.get(m.Customer, o.customer_id)
        e = {"shipment_id": sid, "code": s.code, "vehicle_no": s.vehicle_no, "customer": f"{cus.name} - {cus.city}", "units": n}
        if s.status == "IN_TRANSIT":
            transit.append(e | {"action": "IN_TRANSIT_RECALL"})
        elif s.status == "DELIVERED":
            delivered.append(e | {"action": "NOTIFY_CUSTOMER"})
    passports = list(db.scalars(select(m.Passport.id).where(m.Passport.unit_id.in_(ids), m.Passport.status.in_(("ACTIVE", "HELD")))))
    return {"scope_type": scope_type, "scope_id": entity_id, "scope_label": label, "units": len(ids), "unit_counts": counts,
            "held_package_ids": held_pkgs, "packages_held": len(held_pkgs), "passports_held": len(passports),
            "blocked_at_plant": at_plant, "loaded_not_departed": loaded, "in_transit": transit, "delivered": delivered,
            "query_ms": round((time.perf_counter() - t0) * 1000, 1), "evidence": {"genealogy_query": QUERY, "as_of": _iso(datetime.now(UTC))}}


# ── trace graph ────────────────────────────────────────────────────────────
def trace(db: Session, key: str, type_: str | None = None) -> dict:
    st, eid, label = resolve(db, type_, key)
    ids = unit_ids_in_scope(db, st, eid)
    place = placements(db, ids)
    nodes: dict[str, dict] = {}
    edges: dict[tuple[str, str], dict] = {}
    held = {(h.entity_type, h.entity_id) for h in db.scalars(select(m.RecallHold).where(m.RecallHold.released_at.is_(None)))}

    def node(id_, type__, lbl, status=None, is_held=False, **meta):
        nodes.setdefault(id_, {"id": id_, "type": type__, "label": lbl, "status": status, "held": is_held, "meta": meta})

    def edge(a, b, kind):
        e = edges.setdefault((a, b), {"from": a, "to": b, "type": kind, "units": 0})
        e["units"] += 1

    show_units = st in ("UNIT", "PACKAGE")
    for uid in ids:
        u = db.get(m.Unit, uid)
        lot = db.get(m.Lot, u.lot_id)
        line = db.get(m.Line, lot.line_id)
        node(lot.id, "LOT", lot.lot_code, "CONTAINMENT_ACTIVE" if eligibility.open_containment_runs(db, u) else None, ("LOT", lot.id) in held, line=line.name)
        for hid in db.scalars(select(m.LotHeat.heat_id).where(m.LotHeat.lot_id == lot.id)):
            h = db.get(m.Heat, hid)
            sup = db.get(m.Supplier, h.supplier_id)
            node(sup.id, "SUPPLIER", sup.name, code=sup.code)
            node(h.id, "HEAT", h.heat_code, h.status, ("HEAT", h.id) in held or h.status == "QUARANTINED", grade=h.material_grade, cert=h.certificate_sha256[:12])
            edges.setdefault((sup.id, h.id), {"from": sup.id, "to": h.id, "type": "SUPPLIED", "units": 0})
            edges.setdefault((h.id, lot.id), {"from": h.id, "to": lot.id, "type": "MADE_FROM", "units": 0})
        tail = lot.id
        if show_units:
            node(u.id, "UNIT", u.serial, u.quality_status, ("UNIT", u.id) in held)
            edge(lot.id, u.id, "CONTAINS"); tail = u.id
            pp = eligibility.current_passport(db, u)
            if pp:
                node(pp.id, "PASSPORT", pp.id, pp.status, basis=pp.inspection_basis, four_m_tagged=pp.four_m_tagged)
                edge(u.id, pp.id, "CERTIFIED_BY")
        pkg_id, shp_id, sst = place[uid]
        if not pkg_id:
            continue
        pkg = db.get(m.Package, pkg_id)
        node(pkg.id, "PACKAGE", pkg.label, pkg.state, ("PACKAGE", pkg.id) in held, units=len(list(db.scalars(select(m.PackageItem.id).where(m.PackageItem.package_id == pkg.id, m.PackageItem.removed_at.is_(None))))))
        edge(tail, pkg.id, "PACKED_IN")
        if shp_id:
            s = db.get(m.Shipment, shp_id)
            o = db.get(m.Order, s.order_id)
            cus = db.get(m.Customer, o.customer_id)
            node(s.id, "SHIPMENT", s.code, s.status, vehicle=s.vehicle_no)
            node(cus.id, "CUSTOMER", cus.name, city=cus.city)
            edge(pkg.id, s.id, "LOADED_ON")
            edges.setdefault((s.id, cus.id), {"from": s.id, "to": cus.id, "type": "DELIVERED_TO" if sst == "DELIVERED" else "BOUND_FOR", "units": 0})
    imp = impact(db, st, eid, label)
    summary = {"units": imp["units"], "packages": len({place[u][0] for u in ids if place[u][0]}), "at_plant": imp["unit_counts"]["at_plant"],
               "loaded": imp["unit_counts"]["loaded_not_departed"], "in_transit": imp["unit_counts"]["in_transit"], "delivered": imp["unit_counts"]["delivered"]}
    return {"root": {"type": st, "id": eid, "label": label}, "nodes": list(nodes.values()), "edges": list(edges.values()), "summary": summary}


# ── quarantine ─────────────────────────────────────────────────────────────
def _view(db: Session, r: m.Recall) -> dict:
    tasks = list(db.scalars(select(m.SegregationTask).where(m.SegregationTask.recall_id == r.id).order_by(m.SegregationTask.created_at)))
    return {"id": r.id, "scope": {"type": r.scope_type, "id": r.scope_id, "label": r.impact.get("scope_label")}, "reason": r.reason,
            "executed_by": db.get(m.User, r.actor_id).username, "executed_at": _iso(r.executed_at), "affected": r.impact,
            "unit_counts": r.impact.get("unit_counts"), "held_package_ids": r.impact.get("held_package_ids"), "query_ms": r.impact.get("query_ms"),
            "evidence": r.impact.get("evidence"), "segregation_tasks": [segregation.view(db, t) for t in tasks],
            "audit_event_ids": r.impact.get("audit_event_ids", []), "released_at": _iso(r.released_at),
            "released_by": db.get(m.User, r.released_by).username if r.released_by else None, "release_reason": r.release_reason}


def preview(db: Session, type_: str | None, key: str) -> dict:
    st, eid, label = resolve(db, type_, key)
    return impact(db, st, eid, label) | {"preview": True}


def execute(db: Session, p: Principal, type_: str | None, key: str, reason: str) -> dict:
    if not reason.strip():
        raise ApiError(422, "REASON_REQUIRED", "A quarantine reason is required")
    st, eid, label = resolve(db, type_, key)
    ids = unit_ids_in_scope(db, st, eid)
    place = placements(db, ids)
    lot_ids = sorted(set(db.scalars(select(m.Unit.lot_id).where(m.Unit.id.in_(ids))))) if ids else ([eid] if st == "LOT" else [])
    heat_ids = sorted(set(db.scalars(select(m.LotHeat.heat_id).where(m.LotHeat.lot_id.in_(lot_ids))))) if lot_ids else ([eid] if st == "HEAT" else [])
    if st == "HEAT" and eid not in heat_ids:
        heat_ids = sorted(set(heat_ids) | {eid})
    pkg_ids = sorted({v[0] for v in place.values() if v[0]})
    ship_ids = sorted({v[1] for v in place.values() if v[1]})
    order_ids = sorted(set(db.scalars(select(m.Package.order_id).where(m.Package.id.in_(pkg_ids))))) if pkg_ids else []
    locks = lock_set(db)
    locks.lock(m.Heat, heat_ids); locks.lock(m.Lot, lot_ids); locks.lock(m.Unit, ids)
    locks.lock(m.Package, pkg_ids); locks.lock(m.Order, order_ids); locks.lock(m.Shipment, ship_ids)
    if unit_ids_in_scope(db, st, eid) != ids or placements(db, ids) != place:
        raise ApiError(409, "SCOPE_CHANGED", "Genealogy changed while locking (concurrent pack/load/departure) — retry")
    imp = impact(db, st, eid, label)
    r = m.Recall(id=new_id("rcl"), scope_type=st, scope_id=eid, reason=reason.strip(), impact={}, actor_id=p.id)
    db.add(r); db.flush()
    holds = [(st, eid)] + [("UNIT", u) for u in ids] + [("PACKAGE", pid) for pid in imp["held_package_ids"]]
    for et, e in dict.fromkeys(holds):
        db.add(m.RecallHold(id=new_id("rh"), recall_id=r.id, entity_type=et, entity_id=e))
    if st == "HEAT":
        db.get(m.Heat, eid).status = "QUARANTINED"
    for pp in db.scalars(select(m.Passport).where(m.Passport.unit_id.in_(ids), m.Passport.status == "ACTIVE")):
        pp.status = "HELD"
    db.flush()
    tasks = []
    for e in imp["loaded_not_departed"]:
        tasks.append(segregation.create(db, type="REMOVAL_REQUIRED", dedupe_key=f"removal:{e['package_id']}:{e['shipment_id']}", recall_id=r.id,
                                        package_id=e["package_id"], shipment_id=e["shipment_id"], reason=f"Recall {label}: {reason}"))
    for e in imp["in_transit"]:
        tasks.append(segregation.create(db, type="IN_TRANSIT_RECALL", dedupe_key=f"recall:{r.id}:{e['shipment_id']}", recall_id=r.id, shipment_id=e["shipment_id"],
                                        reason=f"{e['units']} affected unit(s) in transit on {e['vehicle_no']}", consignee_action={"action": "Intercept and quarantine on arrival", **e}))
    for e in imp["delivered"]:
        tasks.append(segregation.create(db, type="NOTIFY_CUSTOMER", dedupe_key=f"notify:{r.id}:{e['shipment_id']}", recall_id=r.id, shipment_id=e["shipment_id"],
                                        reason=f"{e['units']} affected unit(s) delivered to {e['customer']}", consignee_action={"action": "Notify consignee; arrange return", **e}))
    a1 = audit.append(db, p.username, "RECALL_EXECUTED", "recall", r.id, reason,
                      data={"scope": f"{st}:{label}", "units": imp["units"], **{f"units_{k}": v for k, v in imp["unit_counts"].items()}, "packages_held": imp["packages_held"]})
    for pid in imp["held_package_ids"]:
        outbox.emit(db, "DISPATCH_STATE_CHANGED", "PACKAGE", pid, severity="HIGH", reason_codes=["QUARANTINED"], payload={"recall_id": r.id, "held": True})
    raise_alert(db, type="QUARANTINE", severity="CRITICAL", entity_type=st, entity_id=eid, reason_codes=["QUARANTINED"],
                details={"recall_id": r.id, "scope": label, "units": imp["units"], "packages_held": imp["packages_held"],
                         "in_transit": len(imp["in_transit"]), "delivered": len(imp["delivered"])}, dedupe_key=f"recall:{r.id}", audible=True)
    r.impact = imp | {"audit_event_ids": [a1.id], "segregation_task_ids": [t for t in tasks if t]}
    db.flush()
    return _view(db, r)


def release(db: Session, p: Principal, recall_id: str, reason: str) -> dict:
    from app.features.inspection_passports import passports
    if not reason.strip():
        raise ApiError(422, "REASON_REQUIRED", "A release reason is required")
    r0 = db.get(m.Recall, recall_id)
    if not r0:
        raise ApiError(404, "RECALL_NOT_FOUND", "Recall not found")
    holds = list(db.scalars(select(m.RecallHold).where(m.RecallHold.recall_id == recall_id, m.RecallHold.released_at.is_(None))))
    unit_ids = sorted(h.entity_id for h in holds if h.entity_type == "UNIT")
    pkg_ids = sorted(h.entity_id for h in holds if h.entity_type == "PACKAGE")
    locks = lock_set(db)
    if r0.scope_type == "HEAT":
        locks.lock(m.Heat, [r0.scope_id])
    units = locks.lock(m.Unit, unit_ids)
    locks.lock(m.Package, pkg_ids)
    r = db.scalars(select(m.Recall).where(m.Recall.id == recall_id).with_for_update().execution_options(populate_existing=True)).one()
    if r.released_at:
        raise ApiError(409, "RECALL_RELEASED", "Recall already released")
    now = datetime.now(UTC)
    for h in holds:
        h.released_at = now
    db.flush()
    if r.scope_type == "HEAT" and not db.scalars(select(m.RecallHold).where(m.RecallHold.entity_type == "HEAT", m.RecallHold.entity_id == r.scope_id,
                                                                            m.RecallHold.released_at.is_(None))).first():
        db.get(m.Heat, r.scope_id).status = "ACTIVE"
    r.released_at, r.released_by, r.release_reason = now, p.id, reason.strip()
    db.flush()
    reactivated = 0
    for u in units:
        got, _ = passports.issue(db, u, p.username)  # re-activates HELD passports only if nothing else blocks
        reactivated += bool(got and got.status == "ACTIVE")
    audit.append(db, p.username, "RECALL_RELEASED", "recall", r.id, reason, data={"holds_released": len(holds), "passports_active": reactivated})
    return _view(db, r) | {"passports_reactivated": reactivated}


def get(db: Session, recall_id: str) -> dict:
    r = db.get(m.Recall, recall_id)
    if not r:
        raise ApiError(404, "RECALL_NOT_FOUND", "Recall not found")
    return _view(db, r)


def list_(db: Session) -> list[dict]:
    return [_view(db, r) for r in db.scalars(select(m.Recall).order_by(m.Recall.executed_at.desc()))]
