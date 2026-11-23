"""PostgreSQL implementation of app.ports.Ports (Opus). Astra's routers persist only through this."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import audit
from app.core.auth import Principal
from app.core.errors import ApiError
from app.core.ids import new_id
from app.core.signing import list_public_keys
from app.core.storage import put_evidence, sniff
from app.db import models as m
from app.ports import ReportSnapshot, SealContext, SpcBaseline, SpcPoint, SpcRule, VisionProfile


def profile_view(p: m.InspectionProfile) -> VisionProfile:
    return VisionProfile(id=p.id, part_id=p.part_id, station_id=p.station_id, method_version=p.method_version, calibration_version=p.calibration_version,
                         calibrated=p.calibrated, reference_sha256=p.reference_sha256, thresholds=dict(p.thresholds or {}))


def spc_inputs(db: Session, part_id: str, station_id: str, method_version: str) -> tuple[list[SpcPoint], SpcBaseline | None, SpcRule, m.SpcBaseline | None]:
    rows = db.execute(select(m.Inspection.id, m.Inspection.created_at, m.Inspection.raw_score, m.Inspection.evidence_source)
                      .join(m.Unit, m.Unit.id == m.Inspection.unit_id)
                      .where(m.Unit.part_id == part_id, m.Inspection.station_id == station_id, m.Inspection.method_version == method_version,
                             m.Inspection.raw_score.is_not(None)).order_by(m.Inspection.created_at, m.Inspection.id)).all()
    points = [SpcPoint(inspection_id=i, t=t, score=s, synthetic=src != "LIVE_CAPTURE") for i, t, s, src in rows]
    b = db.scalars(select(m.SpcBaseline).where(m.SpcBaseline.part_id == part_id, m.SpcBaseline.station_id == station_id, m.SpcBaseline.method_version == method_version)).first()
    base = SpcBaseline(n=b.n, mean=b.mean, std=b.std, frozen_at=b.frozen_at) if b and b.frozen_at else None
    rule = SpcRule(**(b.rule if b and b.rule else {}))
    return points, base, rule, b


class PgPorts:
    # ── component vision ──
    def list_profiles(self, db: Session, principal: Principal) -> list[VisionProfile]:
        return [profile_view(p) for p in db.scalars(select(m.InspectionProfile).where(m.InspectionProfile.active.is_(True)).order_by(m.InspectionProfile.id))]

    def load_profile(self, db: Session, profile_id: str, principal: Principal) -> VisionProfile:
        p = db.get(m.InspectionProfile, profile_id)
        if not p:
            raise ApiError(404, "PROFILE_NOT_FOUND", "Inspection profile not found")
        return profile_view(p)

    def save_reference(self, db: Session, profile_id: str, image: bytes, media_type: str, principal: Principal) -> VisionProfile:
        """A new reference creates a new, UNCALIBRATED revision; calibration needs an accepted held-out evaluation."""
        old = db.scalars(select(m.InspectionProfile).where(m.InspectionProfile.id == profile_id).with_for_update()).first()
        if not old:
            raise ApiError(404, "PROFILE_NOT_FOUND", "Inspection profile not found")
        sha = put_evidence(db, image, sniff(image) or media_type, "LIVE_CAPTURE", principal.id)
        old.active = False
        new = m.InspectionProfile(id=new_id("prof"), part_id=old.part_id, station_id=old.station_id, method_version=old.method_version,
                                  calibration_version=old.calibration_version + 1, calibrated=False, reference_sha256=sha, thresholds=old.thresholds, active=True)
        db.add(new); db.flush()
        audit.append(db, principal.username, "REFERENCE_ENROLLED", "inspection_profile", new.id, data={"previous": old.id, "reference": sha})
        return profile_view(new)

    # ── seal vision (packaging slice) ──
    def load_seal_context(self, db: Session, package_id: str, principal: Principal) -> SealContext:
        from app.features.packaging.service import load_seal_context
        return load_seal_context(db, package_id)

    def record_recheck(self, db, ctx, evidence, probe, probe_media_type, evidence_source, principal) -> dict:
        from app.features.packaging.service import record_recheck
        return record_recheck(db, ctx, evidence, probe, probe_media_type, evidence_source, principal)

    # ── spc ──
    def load_spc_inputs(self, db: Session, part_id: str, line_id: str, method_version: str, principal: Principal):
        line = db.get(m.Line, line_id)
        if not line:
            raise ApiError(404, "LINE_NOT_FOUND", "Line not found")
        points, base, rule, row = spc_inputs(db, part_id, line.station_id, method_version)
        alerts = []
        if row:
            for w, a in db.execute(select(m.SpcWarning, m.Alert).join(m.Alert, m.Alert.id == m.SpcWarning.alert_id, isouter=True)
                                   .where(m.SpcWarning.baseline_id == row.id).order_by(m.SpcWarning.created_at)):
                alerts.append({"id": a.id if a else w.id, "inspection_id": w.inspection_id, "rules": w.rules, "status": a.status if a else "OPEN",
                               "message": (a.details or {}).get("message") if a else None, "created_at": w.created_at.isoformat()})
        return points, base, rule, alerts

    # ── reports (report-workflow slice) ──
    def load_snapshot(self, db: Session, report_id: str, principal: Principal) -> ReportSnapshot:
        from app.features.report_workflow.service import load_snapshot
        return load_snapshot(db, report_id)

    def record_pdf(self, db, snapshot, pdf, principal) -> str:
        from app.features.report_workflow.service import record_pdf
        return record_pdf(db, snapshot, pdf, principal)

    # ── crypto ──
    def list_public_keys(self, db: Session) -> list[dict]:
        return list_public_keys(db)
