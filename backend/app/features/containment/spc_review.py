"""A3 containment review (Opus side of PR #35). Owner: Opus.

Astra's pure `investigate()` and `recommend_containment()` decide what the evidence supports; this module stores that
answer verbatim with what was read, and runs the human workflow around it:
  PROPOSED  → a Quality Manager APPROVES specific proposed lots (only when the recommendation is REVIEW_RECOMMENDED),
              or REJECTS, with a reason. The decision is final (database-enforced).
  APPROVED  → each approved lot is quarantined by its own call through the normal recall path (preview-able, audited,
              one lot per transaction), and linked back here.
Nothing here widens scope: unresolved exposure stays listed, and a SIGNAL never becomes a hold without a person.
"""
from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import audit
from app.core.auth import Principal
from app.core.errors import ApiError
from app.core.ids import new_id
from app.db import models as m
from app.features.recall import service as recall
from app.features.spc import service as spc
from app.ports import ports


def _jsonable(v):
    if is_dataclass(v):
        return _jsonable(asdict(v))
    if isinstance(v, dict):
        return {k: _jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    if isinstance(v, datetime):
        return v.astimezone(UTC).isoformat().replace("+00:00", "Z")
    return v


def _iso(dt):
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z") if dt else None


def view(db: Session, r: m.SpcReview) -> dict:
    rec = r.recommendation
    lots = {lt.id: lt for lt in db.scalars(select(m.Lot).where(m.Lot.id.in_([p["lot_id"] for p in rec.get("proposed_lots", [])]
                                                                            + [u["entity_id"] for u in rec.get("unresolved", []) if u["entity_type"] == "LOT"])))}
    inspection = db.get(m.Inspection, r.target_inspection_id)
    unit = db.get(m.Unit, inspection.unit_id) if inspection else None
    issues = r.investigation.get("issues", [])
    counts: dict[str, int] = {}
    for i in issues:
        counts[i["code"]] = counts.get(i["code"], 0) + 1
    return {"id": r.id, "alert_id": r.alert_id, "target_inspection_id": r.target_inspection_id, "target_serial": unit.serial if unit else None,
            "status": r.status, "recommendation_status": rec["status"], "investigation_status": rec["investigation_status"],
            "qualification_version": r.qualification_version, "recommendation_version": r.recommendation_version,
            "issue_counts": counts, "recommendation": rec, "read_context": r.read_context,
            "lot_codes": {k: v.lot_code for k, v in lots.items()},
            "approved_lot_ids": r.approved_lot_ids, "recall_ids": r.recall_ids,
            "created_by": db.get(m.User, r.created_by).username, "created_at": _iso(r.created_at),
            "decided_by": db.get(m.User, r.decided_by).username if r.decided_by else None, "decided_at": _iso(r.decided_at),
            "decision_reason": r.decision_reason}


def create(db: Session, p: Principal, inspection_id: str, alert_id: str | None) -> dict:
    if alert_id and not db.get(m.Alert, alert_id):
        raise ApiError(404, "ALERT_NOT_FOUND", "Alert not found")
    port = ports()
    ctx, events, rule = port.load_spc_investigation_inputs(db, inspection_id, p)
    investigation = spc.investigate(ctx, events, inspection_id, rule)
    genealogy = port.load_spc_containment_genealogy(db, investigation, p)
    rec = spc.recommend_containment(investigation, genealogy)
    read = {"as_of": _iso(datetime.now(UTC)), "events_read": len(events), "line_id": genealogy.line_id,
            "lots_read": [lt.lot_id for lt in genealogy.lots], "units_read": len(genealogy.units),
            "four_m_read": [f.event_id for f in genealogy.four_m], "exposure_links_read": 0,
            "note": "No independent exposure evidence is recorded in this system; unsampled lots stay unresolved."}
    r = m.SpcReview(id=new_id("spcr"), alert_id=alert_id, target_inspection_id=inspection_id,
                    qualification_version=investigation.qualification_version, recommendation_version=rec.recommendation_version,
                    investigation=_jsonable(investigation), recommendation=_jsonable(rec), read_context=read, created_by=p.id)
    db.add(r)
    db.flush()
    audit.append(db, p.username, "SPC_REVIEW_CREATED", "spc_review", r.id, data={"inspection": inspection_id, "status": rec.status,
                                                                                  "proposed_lots": [x.lot_id for x in rec.proposed_lots]})
    return view(db, r)


def _locked(db: Session, review_id: str) -> m.SpcReview:
    r = db.scalars(select(m.SpcReview).where(m.SpcReview.id == review_id).with_for_update().execution_options(populate_existing=True)).first()
    if not r:
        raise ApiError(404, "SPC_REVIEW_NOT_FOUND", "SPC review not found")
    return r


def decide(db: Session, p: Principal, review_id: str, decision: str, reason: str, lot_ids: list[str]) -> dict:
    r = _locked(db, review_id)
    if r.status != "PROPOSED":
        raise ApiError(409, "SPC_REVIEW_DECIDED", f"Already {r.status.lower()}")
    if decision == "APPROVE":
        if r.recommendation["status"] != "REVIEW_RECOMMENDED":
            raise ApiError(409, "NOTHING_TO_APPROVE", f"The recommendation is {r.recommendation['status']}: there is no supported scope to approve")
        proposed = {x["lot_id"] for x in r.recommendation["proposed_lots"]}
        chosen = list(dict.fromkeys(lot_ids))
        if not chosen or not set(chosen) <= proposed:
            raise ApiError(422, "LOTS_NOT_PROPOSED", "Approve one or more of the proposed lots (and only those)", {"proposed": sorted(proposed)})
        r.status, r.approved_lot_ids = "APPROVED", chosen
    else:
        r.status = "REJECTED"
    r.decided_by, r.decided_at, r.decision_reason = p.id, datetime.now(UTC), reason
    db.flush()
    audit.append(db, p.username, f"SPC_REVIEW_{r.status}", "spc_review", r.id, reason, data={"lots": r.approved_lot_ids})
    return view(db, r)


def quarantine(db: Session, p: Principal, review_id: str, lot_id: str) -> dict:
    """Quarantine ONE approved lot through the normal recall path (its own transaction and audit trail)."""
    r = _locked(db, review_id)
    if r.status != "APPROVED" or lot_id not in r.approved_lot_ids:
        raise ApiError(409, "LOT_NOT_APPROVED", "Only a lot approved in this review can be quarantined from it")
    if lot_id in (r.recall_ids or {}):
        raise ApiError(409, "ALREADY_QUARANTINED", "This lot was already quarantined from this review", {"recall_id": r.recall_ids[lot_id]})
    rec = recall.execute(db, p, "LOT", lot_id, f"SPC review {r.id}: {r.decision_reason}")
    r.recall_ids = {**(r.recall_ids or {}), lot_id: rec["id"]}
    db.flush()
    return view(db, r)


def list_reviews(db: Session) -> list[dict]:
    return [view(db, r) for r in db.scalars(select(m.SpcReview).order_by(m.SpcReview.created_at.desc()).limit(100))]


def get(db: Session, review_id: str) -> dict:
    r = db.get(m.SpcReview, review_id)
    if not r:
        raise ApiError(404, "SPC_REVIEW_NOT_FOUND", "SPC review not found")
    return view(db, r)
