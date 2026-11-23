"""4M change points and containment runs (CONTRACT §10.1). Owner: Opus.

Rules
- One run per 4M event; overlapping events → independent runs, ALL must clear.
- Scope (frozen at creation, extended only by new production on the line while the run is open):
  * EXISTING_RECHECK: unshipped units on the line created ≥ effective_at − lookback that already PASSED or hold a
    passport, when the policy says to recheck existing stock (default: unplanned yes, planned no);
  * NEW_PRODUCTION: in-window units not yet inspected, plus every unit serialized on the line while the run is open.
  Every scoped unit now requires INDIVIDUAL_100PCT and is 4M-tagged; current passports go HELD.
- Counter: an individual PASS on a distinct scoped unit earns one credit per epoch (retries count once);
  HOLD earns nothing; any REJECT in scope resets the counter and opens a new epoch (evidence is kept).
- READY_FOR_CLEARANCE when credits ≥ required AND every EXISTING_RECHECK unit is resolved (PASS after the event,
  or REJECT). Only a quality_manager with a reason can CLEAR. Passports are minted only after clearance.
"""
from datetime import UTC, datetime, timedelta

from sqlalchemy import exists, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core import audit, outbox
from app.core.auth import Principal
from app.core.errors import ApiError
from app.core.ids import new_id
from app.db import models as m
from app.db.locking import LockSet, lock_set
from app.features.alerts.service import raise_alert
from app.policy import policies

OPEN = ("ACTIVE", "READY_FOR_CLEARANCE")


def _iso(dt):
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z") if dt else None


def _shipped_unit_ids(db: Session):
    return (select(m.PackageItem.unit_id).join(m.Load, (m.Load.package_id == m.PackageItem.package_id) & m.Load.removed_at.is_(None))
            .where(m.PackageItem.removed_at.is_(None)))


def _credits(db: Session, run: m.ContainmentRun) -> int:
    return db.scalar(select(func.count()).select_from(m.ContainmentCredit).where(m.ContainmentCredit.run_id == run.id, m.ContainmentCredit.epoch == run.sequence_epoch))


def _recheck_state(db: Session, run: m.ContainmentRun) -> tuple[int, int, list[str]]:
    ids = list(db.scalars(select(m.ContainmentUnit.unit_id).where(m.ContainmentUnit.run_id == run.id, m.ContainmentUnit.scope_role == "EXISTING_RECHECK")))
    done = 0
    for uid in ids:
        unit = db.get(m.Unit, uid)
        passed_after = db.scalar(select(exists().where(m.Inspection.unit_id == uid, m.Inspection.verdict == "PASS", m.Inspection.created_at >= run.created_at)))
        if unit.quality_status == "REJECT" or (unit.quality_status == "PASS" and passed_after):
            done += 1
    return len(ids), done, ids


def _refresh(db: Session, run: m.ContainmentRun) -> None:
    run.cleared_qty = _credits(db, run)
    total, done, _ = _recheck_state(db, run)
    if run.status != "CLEARED":
        run.status = "READY_FOR_CLEARANCE" if run.cleared_qty >= run.required_qty and done >= total else "ACTIVE"


def view(db: Session, run: m.ContainmentRun) -> dict:
    ev = db.get(m.FourMEvent, run.event_id)
    units = list(db.execute(select(m.ContainmentUnit.unit_id, m.ContainmentUnit.scope_role, m.Unit.lot_id).join(m.Unit, m.Unit.id == m.ContainmentUnit.unit_id)
                            .where(m.ContainmentUnit.run_id == run.id).order_by(m.ContainmentUnit.production_seq)))
    total, done, recheck_ids = _recheck_state(db, run)
    credited = list(db.scalars(select(m.ContainmentCredit.unit_id).where(m.ContainmentCredit.run_id == run.id, m.ContainmentCredit.epoch == run.sequence_epoch)))
    obligations = [{"type": "FULL_INSPECT_FIRST_N", "required": run.required_qty, "completed": min(run.cleared_qty, run.required_qty), "unit_ids": credited}]
    if total:
        obligations.append({"type": "RECHECK_UNIT", "required": total, "completed": done, "unit_ids": recheck_ids})
    cleared_by = db.get(m.User, run.cleared_by).username if run.cleared_by else None
    return {"id": run.id, "event_id": run.event_id, "event_code": ev.code if ev else None, "status": run.status, "line_id": run.line_id,
            "rule": run.rule, "policy_version": run.policy_version, "sequence_epoch": run.sequence_epoch,
            "containment_required_qty": run.required_qty, "containment_cleared_qty": run.cleared_qty,
            "affected_unit_ids": [u for u, _, _ in units], "affected_lot_ids": sorted({lot for _, _, lot in units}),
            "scope": {"existing_recheck": sum(r == "EXISTING_RECHECK" for _, r, _ in units), "new_production": sum(r == "NEW_PRODUCTION" for _, r, _ in units)},
            "obligations": obligations, "cleared_by": cleared_by, "cleared_at": _iso(run.cleared_at), "clear_reason": run.clearance_reason,
            "created_at": _iso(run.created_at)}


def event_view(db: Session, ev: m.FourMEvent) -> dict:
    run = db.scalars(select(m.ContainmentRun).where(m.ContainmentRun.event_id == ev.id)).first()
    return {"id": ev.id, "code": ev.code, "category": ev.category, "planned": ev.planned, "line_id": ev.line_id, "description": ev.description,
            "reason": ev.reason, "effective_at": _iso(ev.effective_at), "policy_version": ev.policy_version,
            "created_by": db.get(m.User, ev.actor_id).username, "created_at": _iso(ev.created_at), "containment_id": run.id if run else None}


def _hold_passports(db: Session, unit_ids: list[str]) -> int:
    n = 0
    for pp in db.scalars(select(m.Passport).where(m.Passport.unit_id.in_(unit_ids), m.Passport.status == "ACTIVE")):
        pp.status = "HELD"
        n += 1
    return n


def log_event(db: Session, p: Principal, category: str, planned: bool, line_id: str, description: str, reason: str, effective_at: datetime | None) -> dict:
    line = db.get(m.Line, line_id)
    if not line:
        raise ApiError(404, "LINE_NOT_FOUND", "Line not found")
    now = datetime.now(UTC)
    eff = effective_at.astimezone(UTC) if effective_at else now
    if eff > now + timedelta(minutes=5):
        raise ApiError(422, "EFFECTIVE_AT_IN_FUTURE", "A change point cannot be logged ahead of time; log it when it happens")
    pol = policies.active(db)
    rule = policies.settings_of(pol).containment
    recheck = rule.recheck_existing_planned if planned else rule.recheck_existing_unplanned
    since = eff - timedelta(minutes=rule.lookback_minutes)

    locks = lock_set(db)
    lot_ids = list(db.scalars(select(m.Lot.id).where(m.Lot.line_id == line.id)))
    locks.lock(m.Lot, lot_ids)
    cand = list(db.scalars(select(m.Unit.id).where(m.Unit.lot_id.in_(lot_ids), m.Unit.created_at >= since, m.Unit.quality_status != "REJECT",
                                                   m.Unit.id.not_in(_shipped_unit_ids(db)))))
    units = locks.lock(m.Unit, cand)
    n = db.scalar(select(func.count()).select_from(m.FourMEvent)) + 1
    ev = m.FourMEvent(id=new_id("fm"), code=f"FM-{n:04d}", line_id=line.id, category=category, planned=planned, description=description.strip(),
                      reason=reason.strip(), effective_at=eff, policy_version=pol.version, actor_id=p.id)
    db.add(ev); db.flush()
    run = m.ContainmentRun(id=new_id("run"), event_id=ev.id, line_id=line.id, required_qty=rule.first_n, cleared_qty=0, sequence_epoch=1, status="ACTIVE",
                           rule={**rule.model_dump(), "recheck_existing": recheck}, policy_version=pol.version)
    db.add(run); db.flush()
    scoped: list[m.Unit] = []
    for u in units:
        has_pp = db.scalar(select(exists().where(m.Passport.unit_id == u.id, m.Passport.status.in_(("ACTIVE", "HELD")))))
        existing = u.quality_status == "PASS" or has_pp
        if existing and not recheck:
            continue
        db.add(m.ContainmentUnit(run_id=run.id, unit_id=u.id, production_seq=u.production_seq, scope_role="EXISTING_RECHECK" if existing else "NEW_PRODUCTION"))
        u.requires_individual_inspection = True
        u.four_m_tagged = True
        scoped.append(u)
    db.flush()
    held = _hold_passports(db, [u.id for u in scoped])
    _refresh(db, run)
    audit.append(db, p.username, "FOUR_M_EVENT_LOGGED", "four_m_event", ev.id, reason, data={"category": category, "planned": planned, "line": line.name})
    audit.append(db, "policy", "CONTAINMENT_OPENED", "containment", run.id, f"Auto-containment for {ev.code}",
                 data={"scoped_units": len(scoped), "passports_held": held, "required": run.required_qty, "policy": pol.version})
    outbox.emit(db, "LOT_4M_CHANGED", "LINE", line.id, severity="HIGH", reason_codes=["CONTAINMENT_ACTIVE"],
                payload={"event_id": ev.id, "code": ev.code, "containment_id": run.id, "status": run.status, "scoped_units": len(scoped)})
    raise_alert(db, type="FOUR_M_HOLD", severity="HIGH", entity_type="LINE", entity_id=line.id, reason_codes=["CONTAINMENT_ACTIVE"],
                details={"event": ev.code, "category": category, "planned": planned, "containment_id": run.id, "scoped_units": len(scoped)},
                dedupe_key=f"4m:{run.id}", audible=not planned)
    return {"event": event_view(db, ev), "containment": view(db, run)}


def open_runs_for_line(db: Session, line_id: str) -> list[str]:
    return list(db.scalars(select(m.ContainmentRun.id).where(m.ContainmentRun.line_id == line_id, m.ContainmentRun.status.in_(OPEN))))


def enroll_new_production(db: Session, locks: LockSet, lot: m.Lot, units: list[m.Unit]) -> list[str]:
    """Called while serializing (caller holds the lot lock, units are new rows): new production on a contained line joins every open run."""
    runs = locks.lock(m.ContainmentRun, open_runs_for_line(db, lot.line_id))
    for run in runs:
        for u in units:
            db.add(m.ContainmentUnit(run_id=run.id, unit_id=u.id, production_seq=u.production_seq, scope_role="NEW_PRODUCTION"))
            u.requires_individual_inspection = True
            u.four_m_tagged = True
    db.flush()
    return [r.id for r in runs]


def runs_for_unit(db: Session, unit_id: str) -> list[str]:
    return list(db.scalars(select(m.ContainmentUnit.run_id).join(m.ContainmentRun, m.ContainmentRun.id == m.ContainmentUnit.run_id)
                           .where(m.ContainmentUnit.unit_id == unit_id, m.ContainmentRun.status.in_(OPEN))))


def on_verdict(db: Session, locks: LockSet, unit: m.Unit, inspection: m.Inspection | None, verdict: str) -> list[dict]:
    """Counter semantics. Caller already holds lot+unit locks; runs are locked next (hierarchy order)."""
    runs = locks.lock(m.ContainmentRun, runs_for_unit(db, unit.id))
    changes = []
    for run in runs:
        before = (run.status, run.sequence_epoch, run.cleared_qty)
        if verdict == "PASS" and inspection is not None:
            db.execute(insert(m.ContainmentCredit).values(run_id=run.id, epoch=run.sequence_epoch, unit_id=unit.id, inspection_id=inspection.id)
                       .on_conflict_do_nothing())
        elif verdict == "REJECT":
            run.sequence_epoch += 1
        db.flush()
        _refresh(db, run)
        if (run.status, run.sequence_epoch, run.cleared_qty) != before:
            changes.append({"run_id": run.id, "status": run.status, "epoch": run.sequence_epoch, "cleared_qty": run.cleared_qty, "reset": verdict == "REJECT"})
    return changes


def emit_changes(db: Session, changes: list[dict], actor: str) -> None:
    """After audit-chain-safe point: record counter resets / readiness."""
    for c in changes:
        if c["reset"]:
            audit.append(db, actor, "CONTAINMENT_COUNTER_RESET", "containment", c["run_id"], "REJECT inside containment scope", data={"epoch": c["epoch"]})
        outbox.emit(db, "LOT_4M_CHANGED", "CONTAINMENT", c["run_id"], severity="MEDIUM" if c["reset"] else "INFO",
                    reason_codes=["COUNTER_RESET"] if c["reset"] else [], payload=c)


def clear(db: Session, p: Principal, run_id: str, reason: str) -> dict:
    from app.features.inspection_passports import passports  # local import: passports imports eligibility only
    if not reason.strip():
        raise ApiError(422, "REASON_REQUIRED", "A clearance reason is required")
    run0 = db.get(m.ContainmentRun, run_id)
    if not run0:
        raise ApiError(404, "CONTAINMENT_NOT_FOUND", "Containment run not found")
    unit_ids = list(db.scalars(select(m.ContainmentUnit.unit_id).where(m.ContainmentUnit.run_id == run_id)))
    locks = lock_set(db)
    locks.lock(m.Lot, set(db.scalars(select(m.Unit.lot_id).where(m.Unit.id.in_(unit_ids)))))
    units = locks.lock(m.Unit, unit_ids)
    [run] = locks.lock(m.ContainmentRun, [run_id])
    if run.status == "CLEARED":
        raise ApiError(409, "NOT_ACTIVE", "Containment already cleared")
    _refresh(db, run)
    if run.status != "READY_FOR_CLEARANCE":
        v = view(db, run)
        unmet = [{k: o[k] for k in ("type", "required", "completed")} for o in v["obligations"] if o["completed"] < o["required"]]
        raise ApiError(409, "CONTAINMENT_NOT_SATISFIED", f"{len(unmet)} obligation(s) unmet", {"unmet": unmet, "epoch": run.sequence_epoch})
    run.status, run.cleared_by, run.cleared_at, run.clearance_reason = "CLEARED", p.id, datetime.now(UTC), reason.strip()
    db.flush()
    issued = 0
    for u in units:
        got, _ = passports.issue(db, u, p.username)
        issued += bool(got and got.four_m_tagged and got.status == "ACTIVE")
    audit.append(db, p.username, "CONTAINMENT_CLEARED", "containment", run.id, reason, data={"epoch": run.sequence_epoch, "tagged_passports": issued})
    outbox.emit(db, "LOT_4M_CHANGED", "CONTAINMENT", run.id, severity="INFO", payload={"status": "CLEARED", "tagged_passports": issued})
    return view(db, run) | {"tagged_passports_issued": issued}
