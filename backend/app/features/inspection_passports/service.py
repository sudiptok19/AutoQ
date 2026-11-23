"""Inspection verdict policy, HOLD review, lot-sample state and passport release (CONTRACT §4.1, §8.1, §10.1-10.2).
Owner: Opus. Vision is evidence only; `decide()` is the single place a verdict is made."""
from dataclasses import replace
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.ports_impl import profile_view, spc_inputs
from app.core import audit
from app.core.auth import Principal
from app.core.errors import ApiError
from app.core.ids import new_id
from app.core.storage import get_evidence, put_evidence, sniff
from app.db import models as m
from app.db.locking import lock_set
from app.features.alerts.service import raise_alert
from app.features.containment import service as containment
from app.features.inspection_passports import interim_vision as vision  # swapped for Astra's A1 on merge
from app.features.inspection_passports import passports
from app.features.spc.service import compute_series, freeze_baseline
from app.policy import eligibility, policies

SOURCES = {"LIVE_CAPTURE", "PRERECORDED_EVAL", "SYNTHETIC_TEST"}
HOLD_CODES = {"IMAGE_QUALITY_BLUR", "IMAGE_QUALITY_GLARE", "IMAGE_QUALITY_RESOLUTION", "MODEL_UNCALIBRATED", "ALIGNMENT_FAILED", "SCORE_ABOVE_HOLD"}
MESSAGES = {
    "IMAGE_QUALITY_BLUR": "Image too blurred for a reliable measurement", "IMAGE_QUALITY_GLARE": "Glare washes out the part",
    "IMAGE_QUALITY_RESOLUTION": "Resolution below the minimum", "MODEL_UNCALIBRATED": "No calibrated reference for this part/station/method — fails safe to HOLD",
    "ALIGNMENT_FAILED": "Could not register the part against the reference", "SCORE_ABOVE_HOLD": "Anomaly score at or above the hold threshold",
    "SCORE_ABOVE_REJECT": "Anomaly score at or above the reject threshold",
}


def decide(reason_codes: list[str]) -> str:
    """Deterministic verdict from evidence codes. Anything uncertain is HOLD; nothing uncertain is PASS."""
    if "SCORE_ABOVE_REJECT" in reason_codes:
        return "REJECT"
    if any(c in HOLD_CODES for c in reason_codes) or any(c not in MESSAGES for c in reason_codes):
        return "HOLD"
    return "PASS"


def _active_profile(db: Session, part_id: str, station_id: str) -> m.InspectionProfile | None:
    return db.scalars(select(m.InspectionProfile).where(m.InspectionProfile.part_id == part_id, m.InspectionProfile.station_id == station_id,
                                                        m.InspectionProfile.active.is_(True)).order_by(m.InspectionProfile.calibration_version.desc())).first()


def _iso(dt):
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z") if dt else None


def ev_url(sha: str | None) -> str | None:
    return f"/api/v1/evidence/{sha}" if sha else None


def view(db: Session, i: m.Inspection, passport: m.Passport | None = None, extra: dict | None = None) -> dict:
    review = db.scalars(select(m.InspectionReview).where(m.InspectionReview.inspection_id == i.id)).first()
    reviewer = db.get(m.User, review.reviewer_id).username if review else None
    return {"id": i.id, "unit_id": i.unit_id, "lot_id": i.lot_id, "station_id": i.station_id, "created_by": db.get(m.User, i.actor_id).username,
            "created_at": _iso(i.created_at), "profile_id": i.profile_id, "policy_version": i.policy_version, "evidence_source": i.evidence_source,
            "synthetic": i.evidence_source != "LIVE_CAPTURE",
            "evidence": {"image_sha256": i.image_sha256, "image_url": ev_url(i.image_sha256), "aligned_url": ev_url(i.aligned_sha256), "heatmap_url": ev_url(i.diff_sha256)},
            "quality_gate": i.quality_gate, "analysis": i.analysis, "verdict": i.verdict, "reasons": i.reasons,
            "review": {"action": review.action, "reason": review.reason, "by": reviewer, "at": _iso(review.created_at)} if review else None,
            "passport": passports.view(db, passport) if passport else None, **(extra or {})}


def _refresh_sample_status(db: Session, lot: m.Lot) -> str:
    ids = list(db.scalars(select(m.LotSample.unit_id).where(m.LotSample.lot_id == lot.id)))
    if not ids:
        return lot.sample_status
    verdicts = []
    for uid in ids:
        latest = passports.latest_inspection(db, uid)
        unit = db.get(m.Unit, uid)
        verdicts.append("REJECT" if unit.quality_status == "REJECT" else latest.verdict if latest else None)
    new = "FAILED" if "REJECT" in verdicts else "PASSED" if all(v == "PASS" for v in verdicts) else "IN_PROGRESS"
    lot.sample_status = new
    return new


def _update_spc(db: Session, insp: m.Inspection, part_id: str) -> list[dict]:
    if insp.raw_score is None:
        return []
    points, base, rule, row = spc_inputs(db, part_id, insp.station_id, insp.method_version)
    if row is None:
        row = m.SpcBaseline(id=new_id("spcb"), part_id=part_id, station_id=insp.station_id, method_version=insp.method_version, status="COLLECTING", n=0,
                            rule={"k_sigma": rule.k_sigma, "h_sigma": rule.h_sigma, "min_n": rule.min_n, "min_std": rule.min_std})
        db.add(row); db.flush()
    if row.frozen_at is None:
        frozen = freeze_baseline(points, rule)
        if frozen:
            row.n, row.mean, row.std, row.frozen_at = frozen.n, frozen.mean, frozen.std, frozen.frozen_at
            base = frozen
        else:
            row.n = len(points)
    series = compute_series(points, base, rule)
    row.status = series.baseline["status"]
    out = []
    for w in series.warnings:
        if w["inspection_id"] != insp.id:
            continue
        aid = raise_alert(db, type="SPC_DRIFT", severity="MEDIUM", entity_type="STATION", entity_id=insp.station_id, reason_codes=w["rules"],
                          details={"message": w["message"], "inspection_id": insp.id, "baseline_id": row.id, "method_version": insp.method_version},
                          dedupe_key=f"spc:{row.id}:{insp.id}", event_type="SPC_DRIFT_WARNING")
        db.add(m.SpcWarning(id=new_id("spcw"), baseline_id=row.id, inspection_id=insp.id, rules=w["rules"], alert_id=aid))
        out.append(w)
    db.flush()
    return out


def inspect(db: Session, p: Principal, unit_key: str, station_id: str | None, image: bytes, evidence_source: str) -> dict:
    if evidence_source not in SOURCES:
        raise ApiError(422, "EVIDENCE_SOURCE_INVALID", f"evidence_source must be one of {sorted(SOURCES)}")
    media = sniff(image)
    if media not in ("image/jpeg", "image/png", "image/webp"):
        raise ApiError(415, "INVALID_MEDIA", "Inspection image must be JPEG, PNG or WebP")
    unit = db.get(m.Unit, unit_key) or db.scalars(select(m.Unit).where(m.Unit.serial == unit_key)).first()
    if not unit:
        raise ApiError(404, "UNIT_NOT_FOUND", "Unit not found")
    locks = lock_set(db)
    [lot] = locks.lock(m.Lot, [unit.lot_id])
    [unit] = locks.lock(m.Unit, [unit.id])
    line = db.get(m.Line, lot.line_id)
    station = station_id or line.station_id
    if station != line.station_id:
        raise ApiError(409, "STATION_MISMATCH", f"Lot {lot.lot_code} is produced on {line.name} (station {line.station_id})")
    if not lot.frozen_at:
        raise ApiError(409, "LOT_NOT_FROZEN", "Freeze the lot and select its sample before inspection")
    if unit.quality_status == "REJECT":
        raise ApiError(409, "UNIT_REJECTED", "Rejected units cannot be re-inspected")
    last = passports.latest_inspection(db, unit.id)
    if unit.quality_status == "HOLD":
        rv = db.scalars(select(m.InspectionReview).where(m.InspectionReview.inspection_id == last.id)).first() if last else None
        if not rv or rv.action != "REINSPECT":
            raise ApiError(409, "HOLD_REVIEW_REQUIRED", "A quality engineer must review this HOLD before reinspection", {"inspection_id": last.id if last else None})

    pol = policies.active(db)
    ps = policies.settings_of(pol).inspection
    prof_row = _active_profile(db, unit.part_id, station)
    profile = profile_view(prof_row) if prof_row else None
    contained = bool(passports.open_runs(db, unit))
    if profile:
        th = {**profile.thresholds}
        th.setdefault("hold", ps.hold_threshold); th.setdefault("reject", ps.reject_threshold)
        th |= {"blur_min": ps.blur_min, "glare_max": ps.glare_max, "min_w": ps.min_w, "min_h": ps.min_h}
        tight = policies.settings_of(pol).containment.threshold_tightening
        if contained and tight > 0 and profile.calibrated:
            th |= {"hold": th["hold"] * (1 - tight), "reject": th["reject"] * (1 - tight), "tightened": True}
        # a profile built for another method version cannot calibrate this one
        profile = replace(profile, thresholds=th, calibrated=profile.calibrated and profile.method_version == vision.METHOD_VERSION)
    reference = get_evidence(db, profile.reference_sha256)[0] if profile and profile.calibrated and profile.reference_sha256 else None
    ev = vision.evaluate(image, reference, profile if profile else None)
    if not profile:
        ev = vision.VisionEvidence(ev.quality_gate, ev.analysis | {"hold_threshold": ps.hold_threshold, "reject_threshold": ps.reject_threshold}, ev.reason_codes, ev.aligned_png, ev.diff_png, ev.raw_score)

    img_sha = put_evidence(db, image, media, evidence_source, p.id)
    aligned_sha = put_evidence(db, ev.aligned_png, "image/png", evidence_source, p.id) if ev.aligned_png else None
    diff_sha = put_evidence(db, ev.diff_png, "image/png", evidence_source, p.id) if ev.diff_png else None
    verdict = decide(ev.reason_codes)
    reasons = [{"code": c, "message": MESSAGES.get(c, c)} for c in ev.reason_codes]
    insp = m.Inspection(id=new_id("insp"), unit_id=unit.id, lot_id=lot.id, station_id=station, profile_id=prof_row.id if prof_row else None,
                        method_version=ev.analysis["method_version"], image_sha256=img_sha, aligned_sha256=aligned_sha, diff_sha256=diff_sha,
                        quality_gate=ev.quality_gate, analysis=ev.analysis, raw_score=ev.raw_score, verdict=verdict, reasons=reasons,
                        evidence_source=evidence_source, policy_version=pol.version, actor_id=p.id, created_at=datetime.now(UTC))
    db.add(insp)
    unit.quality_status = verdict
    db.flush()

    sample_before = lot.sample_status
    sample_after = _refresh_sample_status(db, lot)
    pp, blockers, released = None, [], 0
    newly_passed = sample_after == "PASSED" and sample_before != "PASSED"
    # Take every row lock BEFORE any audit append (issuance audits), per the lock hierarchy: units → containment runs.
    others = locks.lock(m.Unit, list(db.scalars(select(m.Unit.id).where(m.Unit.lot_id == lot.id, m.Unit.id != unit.id)))) if newly_passed else []
    run_changes = containment.on_verdict(db, locks, unit, insp, verdict)
    if verdict == "PASS":
        pp, blockers = passports.issue(db, unit, p.username)
    elif verdict == "REJECT":
        passports.revoke(db, unit, p.username, "Unit rejected by inspection")
    for u in others:
        got, _ = passports.issue(db, u, p.username)
        released += bool(got and got.inspection_basis == "SAMPLED_LOT")
    drift = _update_spc(db, insp, unit.part_id)
    containment.emit_changes(db, run_changes, p.username)
    audit.append(db, p.username, "INSPECTION_RECORDED", "unit", unit.id,
                 data={"inspection": insp.id, "verdict": verdict, "score": ev.raw_score, "gate": ev.quality_gate["passed"], "policy": pol.version,
                       "evidence_source": evidence_source, "sample_status": sample_after})
    if sample_after == "FAILED" and sample_before != "FAILED":
        raise_alert(db, type="SAMPLE_FAILED", severity="HIGH", entity_type="LOT", entity_id=lot.id, reason_codes=["SAMPLE_FAILED"],
                    details={"lot_code": lot.lot_code, "inspection_id": insp.id}, dedupe_key=f"sample_failed:{lot.id}")
    return view(db, insp, pp, {"issuance_blockers": blockers, "lot_sample_status": sample_after, "sampled_passports_released": released,
                               "contained": contained, "drift_warnings": drift, "containment_progress": run_changes})


def review(db: Session, p: Principal, inspection_id: str, action: str, reason: str) -> dict:
    if not reason.strip():
        raise ApiError(422, "REASON_REQUIRED", "A reason is required")
    insp = db.get(m.Inspection, inspection_id)
    if not insp:
        raise ApiError(404, "INSPECTION_NOT_FOUND", "Inspection not found")
    locks = lock_set(db)
    [lot] = locks.lock(m.Lot, [insp.lot_id])
    [unit] = locks.lock(m.Unit, [insp.unit_id])
    latest = passports.latest_inspection(db, unit.id)
    if insp.verdict != "HOLD" or unit.quality_status != "HOLD" or latest.id != insp.id:
        raise ApiError(409, "NOT_ON_HOLD", "Only the unit's latest HOLD inspection can be reviewed")
    if db.scalars(select(m.InspectionReview).where(m.InspectionReview.inspection_id == insp.id)).first():
        raise ApiError(409, "ALREADY_REVIEWED", "This HOLD was already reviewed")
    db.add(m.InspectionReview(id=new_id("rev"), inspection_id=insp.id, action=action, reason=reason.strip(), reviewer_id=p.id))
    run_changes = []
    if action == "REJECT":
        unit.quality_status = "REJECT"
        db.flush()
        _refresh_sample_status(db, lot)
        run_changes = containment.on_verdict(db, locks, unit, None, "REJECT")
        passports.revoke(db, unit, p.username, reason)
        containment.emit_changes(db, run_changes, p.username)
    db.flush()
    audit.append(db, p.username, f"HOLD_REVIEW_{action}", "unit", unit.id, reason, data={"inspection": insp.id})
    return view(db, insp)


def issue_explicit(db: Session, p: Principal, unit_key: str) -> dict:
    unit = db.get(m.Unit, unit_key) or db.scalars(select(m.Unit).where(m.Unit.serial == unit_key)).first()
    if not unit:
        raise ApiError(404, "UNIT_NOT_FOUND", "Unit not found")
    locks = lock_set(db)
    locks.lock(m.Lot, [unit.lot_id])
    [unit] = locks.lock(m.Unit, [unit.id])
    pp, blockers = passports.issue(db, unit, p.username)
    if not pp:
        code = blockers[0]["code"] if len(blockers) == 1 else "ISSUANCE_BLOCKED"
        raise ApiError(409, code, "Passport cannot be issued", {"blockers": blockers})
    return passports.view(db, pp)


def list_inspections(db: Session, unit_id: str | None, lot_id: str | None, limit: int) -> list[dict]:
    q = select(m.Inspection).order_by(m.Inspection.created_at.desc(), m.Inspection.id.desc()).limit(limit)
    if unit_id:
        q = q.where(m.Inspection.unit_id == unit_id)
    if lot_id:
        q = q.where(m.Inspection.lot_id == lot_id)
    return [view(db, i) for i in db.scalars(q)]


def unit_detail(db: Session, unit: m.Unit) -> dict:
    pp = eligibility.current_passport(db, unit)
    history = list(db.scalars(select(m.Passport).where(m.Passport.unit_id == unit.id).order_by(m.Passport.issued_at)))
    return {"passport": passports.view(db, pp) if pp else None, "passport_history": [passports.view(db, x) for x in history],
            "inspections": list_inspections(db, unit.id, None, 50)}
