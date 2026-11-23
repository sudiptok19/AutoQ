"""Catalog, material heats, lots, serialized units and frozen sampling (CONTRACT §3, §10.2). Owner: Opus."""
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import audit
from app.core.auth import Principal
from app.core.errors import ApiError
from app.core.ids import new_id
from app.core.storage import put_evidence
from app.db import models as m
from app.db.locking import lock_set
from app.policy import eligibility, policies

MAX_UNITS_PER_CALL = 500


def _iso(dt: datetime | None) -> str | None:
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z") if dt else None


def _get(db: Session, cls, id_: str, what: str):
    row = db.get(cls, id_)
    if not row:
        raise ApiError(404, f"{what.upper()}_NOT_FOUND", f"{what.capitalize()} not found", {"id": id_})
    return row


def _create(db: Session, row, conflict_code: str, message: str):
    try:
        with db.begin_nested():
            db.add(row)
            db.flush()
    except IntegrityError:
        raise ApiError(409, conflict_code, message)
    return row


# ── master data ────────────────────────────────────────────────────────────
def supplier_view(s: m.Supplier) -> dict:
    return {"id": s.id, "code": s.code, "name": s.name, "synthetic": s.synthetic}


def part_view(p: m.Part) -> dict:
    return {"id": p.id, "part_number": p.part_number, "name": p.name, "drawing_rev": p.drawing_rev, "is_a_part": p.is_a_part,
            "default_sample_size": p.default_sample_size, "inspection_profile_id": p.inspection_profile_id}


def line_view(x: m.Line) -> dict:
    return {"id": x.id, "name": x.name, "station_id": x.station_id}


def customer_view(c: m.Customer) -> dict:
    return {"id": c.id, "name": c.name, "city": c.city}


def create_supplier(db: Session, p: Principal, code: str, name: str, synthetic: bool) -> dict:
    s = _create(db, m.Supplier(id=new_id("sup"), code=code, name=name, synthetic=synthetic), "SUPPLIER_EXISTS", f"Supplier code {code} exists")
    audit.append(db, p.username, "SUPPLIER_CREATED", "supplier", s.id, data={"code": code})
    return supplier_view(s)


def create_part(db: Session, p: Principal, **f) -> dict:
    part = _create(db, m.Part(id=new_id("part"), **f), "PART_EXISTS", f"Part {f['part_number']} exists")
    audit.append(db, p.username, "PART_CREATED", "part", part.id, data={"part_number": part.part_number, "is_a_part": part.is_a_part})
    return part_view(part)


def create_line(db: Session, p: Principal, name: str, station_id: str) -> dict:
    line = _create(db, m.Line(id=new_id("line"), name=name, station_id=station_id), "LINE_EXISTS", "Line name or station already exists")
    audit.append(db, p.username, "LINE_CREATED", "line", line.id, data={"station_id": station_id})
    return line_view(line)


def create_customer(db: Session, p: Principal, name: str, city: str) -> dict:
    c = m.Customer(id=new_id("cus"), name=name, city=city)
    db.add(c); db.flush()
    audit.append(db, p.username, "CUSTOMER_CREATED", "customer", c.id)
    return customer_view(c)


# ── heats ──────────────────────────────────────────────────────────────────
def heat_view(h: m.Heat) -> dict:
    return {"id": h.id, "heat_code": h.heat_code, "supplier_id": h.supplier_id, "material_grade": h.material_grade, "certificate_sha256": h.certificate_sha256,
            "certificate_name": h.certificate_name, "status": h.status, "held": h.status == "QUARANTINED", "received_at": _iso(h.received_at)}


def create_heat(db: Session, p: Principal, heat_code: str, supplier_id: str, material_grade: str, cert: bytes, cert_name: str, cert_media_type: str) -> dict:
    _get(db, m.Supplier, supplier_id, "supplier")
    sha = put_evidence(db, cert, cert_media_type, "UPLOAD", p.id)
    h = _create(db, m.Heat(id=new_id("heat"), heat_code=heat_code, supplier_id=supplier_id, material_grade=material_grade, certificate_sha256=sha, certificate_name=cert_name[:200]),
                "HEAT_EXISTS", f"Heat {heat_code} exists")
    audit.append(db, p.username, "HEAT_RECEIVED", "heat", h.id, data={"heat_code": heat_code, "certificate_sha256": sha})
    return heat_view(h)


# ── lots & units ───────────────────────────────────────────────────────────
def _held_entities(db: Session) -> set[tuple[str, str]]:
    return {(t, i) for t, i in db.execute(select(m.RecallHold.entity_type, m.RecallHold.entity_id).where(m.RecallHold.released_at.is_(None)))}


def lot_view(db: Session, lot: m.Lot) -> dict:
    counts = {"UNINSPECTED": 0, "PASS": 0, "HOLD": 0, "REJECT": 0}
    for status, n in db.execute(select(m.Unit.quality_status, func.count()).where(m.Unit.lot_id == lot.id).group_by(m.Unit.quality_status)):
        counts[status] = n
    heat_ids = list(db.scalars(select(m.LotHeat.heat_id).where(m.LotHeat.lot_id == lot.id).order_by(m.LotHeat.heat_id)))
    runs = list(db.scalars(select(m.ContainmentRun.id).join(m.ContainmentUnit, m.ContainmentUnit.run_id == m.ContainmentRun.id)
                           .join(m.Unit, m.Unit.id == m.ContainmentUnit.unit_id).where(m.Unit.lot_id == lot.id, m.ContainmentRun.status != "CLEARED").distinct()))
    held = _held_entities(db)
    samples = list(db.scalars(select(m.LotSample.unit_id).join(m.Unit, m.Unit.id == m.LotSample.unit_id).where(m.LotSample.lot_id == lot.id).order_by(m.Unit.production_seq)))
    return {"id": lot.id, "lot_code": lot.lot_code, "part_id": lot.part_id, "line_id": lot.line_id, "heat_ids": heat_ids, "planned_qty": lot.planned_qty,
            "produced_qty": sum(counts.values()), "counts": counts, "containment_ids": runs,
            "held": ("LOT", lot.id) in held or any(("HEAT", h) in held for h in heat_ids),
            "frozen_at": _iso(lot.frozen_at), "policy_version": lot.policy_version, "sample_status": lot.sample_status, "sample_unit_ids": samples,
            "created_at": _iso(lot.created_at)}


def create_lot(db: Session, p: Principal, lot_code: str, part_id: str, line_id: str, heat_ids: list[str], planned_qty: int) -> dict:
    if len(set(heat_ids)) != 1:
        raise ApiError(422, "LOT_SINGLE_HEAT_REQUIRED", "New lots take exactly one material heat", {"heat_ids": heat_ids})
    _get(db, m.Part, part_id, "part"); _get(db, m.Line, line_id, "line")
    heat = _get(db, m.Heat, heat_ids[0], "heat")
    if heat.status == "QUARANTINED":
        raise ApiError(409, "HEAT_QUARANTINED", f"Heat {heat.heat_code} is quarantined")
    lot = _create(db, m.Lot(id=new_id("lot"), lot_code=lot_code, part_id=part_id, line_id=line_id, planned_qty=planned_qty), "LOT_EXISTS", f"Lot {lot_code} exists")
    db.add(m.LotHeat(lot_id=lot.id, heat_id=heat.id)); db.flush()
    audit.append(db, p.username, "LOT_CREATED", "lot", lot.id, data={"lot_code": lot_code, "heat": heat.heat_code, "planned_qty": planned_qty})
    return lot_view(db, lot)


def serialize_units(db: Session, p: Principal, lot_id: str, count: int) -> list[dict]:
    if not 1 <= count <= MAX_UNITS_PER_CALL:
        raise ApiError(422, "VALIDATION", f"count must be 1..{MAX_UNITS_PER_CALL}")
    locks = lock_set(db)
    [lot] = locks.lock(m.Lot, [lot_id]) or [None]
    if lot is None:
        raise ApiError(404, "LOT_NOT_FOUND", "Lot not found")
    if lot.frozen_at:
        raise ApiError(409, "LOT_FROZEN", f"Lot {lot.lot_code} membership is frozen; new production enters a new lot")
    part = db.get(m.Part, lot.part_id)
    start = db.scalar(select(func.coalesce(func.max(m.Unit.production_seq), 0)).where(m.Unit.lot_id == lot.id))
    if start + count > lot.planned_qty:
        raise ApiError(409, "LOT_PLANNED_QTY_EXCEEDED", f"{start + count} > planned {lot.planned_qty}", {"produced": start, "planned": lot.planned_qty})
    units = [m.Unit(id=new_id("unit"), serial=f"{lot.lot_code}-{seq:04d}", lot_id=lot.id, part_id=lot.part_id, production_seq=seq,
                    requires_individual_inspection=part.is_a_part) for seq in range(start + 1, start + count + 1)]
    db.add_all(units); db.flush()
    from app.features.containment.service import enroll_new_production
    runs = enroll_new_production(db, locks, lot, units)
    audit.append(db, p.username, "UNITS_SERIALIZED", "lot", lot.id, data={"from_seq": start + 1, "to_seq": start + count, "containment_runs": runs})
    return [unit_view(db, u) for u in units]


def unit_view(db: Session, u: m.Unit, *, detail: bool = False) -> dict:
    pp = eligibility.current_passport(db, u)
    pkg = db.scalar(select(m.PackageItem.package_id).where(m.PackageItem.unit_id == u.id, m.PackageItem.removed_at.is_(None)))
    sampled = db.get(m.LotSample, (u.lot_id, u.id)) is not None
    e = eligibility.compute(db, u)
    out = {"id": u.id, "serial": u.serial, "lot_id": u.lot_id, "part_id": u.part_id, "production_seq": u.production_seq, "quality_status": u.quality_status,
           "requires_individual_inspection": u.requires_individual_inspection, "four_m_tagged": u.four_m_tagged, "sampled": sampled,
           "passport_id": pp.id if pp else None, "package_id": pkg, "held": any(b["code"] == "QUARANTINED" for b in e.blockers),
           "eligibility": e.as_dict(), "created_at": _iso(u.created_at)}
    return out


def find_unit(db: Session, key: str) -> m.Unit:
    u = db.get(m.Unit, key) or db.scalars(select(m.Unit).where(m.Unit.serial == key)).first()
    if not u:
        raise ApiError(404, "UNIT_NOT_FOUND", "Unit not found", {"key": key})
    return u


def _systematic(n_units: int, k: int) -> list[int]:
    """Evenly spaced, reproducible 0-based indices that always include first and last piece."""
    if k >= n_units:
        return list(range(n_units))
    if k == 1:
        return [0]
    return sorted({round(i * (n_units - 1) / (k - 1)) for i in range(k)})


def freeze_and_sample(db: Session, p: Principal, lot_id: str) -> dict:
    [lot] = lock_set(db).lock(m.Lot, [lot_id]) or [None]
    if lot is None:
        raise ApiError(404, "LOT_NOT_FOUND", "Lot not found")
    if lot.frozen_at:  # idempotent: return the already-frozen selection
        return {"lot_id": lot.id, "frozen_at": _iso(lot.frozen_at), "policy_version": lot.policy_version, "inspection_basis": _basis(db, lot),
                "sample_unit_ids": lot_view(db, lot)["sample_unit_ids"], "replay": True}
    units = list(db.scalars(select(m.Unit).where(m.Unit.lot_id == lot.id).order_by(m.Unit.production_seq)))
    if not units:
        raise ApiError(409, "LOT_EMPTY", "Serialize units before freezing the lot")
    pol = policies.active(db)
    part = db.get(m.Part, lot.part_id)
    lot.frozen_at = datetime.now(UTC)
    lot.policy_version = pol.version
    sample: list[m.Unit] = []
    if part.is_a_part:
        lot.sample_status = "NOT_STARTED"  # every unit needs its own inspection; no lot sample exists
    else:
        k = min(policies.settings_of(pol).sampling.sample_size, part.default_sample_size or 10**6, len(units))
        sample = [units[i] for i in _systematic(len(units), k)]
        db.add_all(m.LotSample(lot_id=lot.id, unit_id=u.id, policy_version=pol.version) for u in sample)
        lot.sample_status = "IN_PROGRESS"
    db.flush()
    audit.append(db, p.username, "LOT_FROZEN", "lot", lot.id, data={"policy_version": pol.version, "units": len(units), "sample": [u.serial for u in sample], "a_part": part.is_a_part})
    return {"lot_id": lot.id, "frozen_at": _iso(lot.frozen_at), "policy_version": pol.version, "inspection_basis": _basis(db, lot),
            "sample_unit_ids": [u.id for u in sample], "replay": False}


def _basis(db: Session, lot: m.Lot) -> str:
    return "INDIVIDUAL_100PCT" if db.get(m.Part, lot.part_id).is_a_part else "SAMPLED_LOT"
