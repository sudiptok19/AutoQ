"""Single authoritative computation of dispatch eligibility (CONTRACT §4.3 / §10.1-10.2).
Never stored; every gate and view recomputes it. Blockers are exhaustive so the UI can say why."""
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import models as m


@dataclass
class Eligibility:
    dispatch_eligible: bool
    blockers: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"dispatch_eligible": self.dispatch_eligible, "blockers": self.blockers}


def active_holds(db: Session, unit: m.Unit) -> list[m.RecallHold]:
    heat_ids = select(m.LotHeat.heat_id).where(m.LotHeat.lot_id == unit.lot_id)
    pkg_ids = select(m.PackageItem.package_id).where(m.PackageItem.unit_id == unit.id, m.PackageItem.removed_at.is_(None))
    q = select(m.RecallHold).where(m.RecallHold.released_at.is_(None)).where(
        ((m.RecallHold.entity_type == "UNIT") & (m.RecallHold.entity_id == unit.id))
        | ((m.RecallHold.entity_type == "LOT") & (m.RecallHold.entity_id == unit.lot_id))
        | ((m.RecallHold.entity_type == "HEAT") & m.RecallHold.entity_id.in_(heat_ids))
        | ((m.RecallHold.entity_type == "PACKAGE") & m.RecallHold.entity_id.in_(pkg_ids))
    )
    return list(db.scalars(q))


def open_containment_runs(db: Session, unit: m.Unit) -> list[m.ContainmentRun]:
    q = (select(m.ContainmentRun).join(m.ContainmentUnit, m.ContainmentUnit.run_id == m.ContainmentRun.id)
         .where(m.ContainmentUnit.unit_id == unit.id, m.ContainmentRun.status != "CLEARED"))
    return list(db.scalars(q))


def current_passport(db: Session, unit: m.Unit) -> m.Passport | None:
    return db.scalars(select(m.Passport).where(m.Passport.unit_id == unit.id, m.Passport.status.in_(("ACTIVE", "HELD")))).first()


def compute(db: Session, unit: m.Unit) -> Eligibility:
    b: list[dict] = []
    pp = current_passport(db, unit)
    if not pp:
        revoked = db.scalars(select(m.Passport.id).where(m.Passport.unit_id == unit.id, m.Passport.status == "REVOKED")).first()
        b.append({"code": "PASSPORT_REVOKED", "message": "Passport revoked"} if revoked else {"code": "NO_PASSPORT", "message": "No quality passport issued"})
    if pp and pp.status == "HELD":
        b.append({"code": "PASSPORT_HELD", "message": "Passport held pending 4M recheck", "ref": pp.id})
    if unit.quality_status == "REJECT":
        b.append({"code": "UNIT_REJECTED", "message": "Unit rejected"})
    elif unit.quality_status == "HOLD":
        b.append({"code": "UNIT_ON_HOLD", "message": "Latest inspection is HOLD"})
    elif pp and pp.inspection_basis == "INDIVIDUAL_100PCT" and unit.quality_status != "PASS":
        b.append({"code": "UNIT_NOT_PASS", "message": f"Quality status is {unit.quality_status}"})
    for run in open_containment_runs(db, unit):
        b.append({"code": "CONTAINMENT_ACTIVE", "message": f"4M containment run {run.id} is {run.status}", "ref": run.id})
    for h in active_holds(db, unit):
        b.append({"code": "QUARANTINED", "message": f"Recall hold on {h.entity_type.lower()} {h.entity_id}", "ref": h.recall_id})
    return Eligibility(not b, b)
