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
        old = db.scalars(select(m.InspectionProfile).where(m.InspectionProfile.id == profile_id).with_for_update().execution_options(populate_existing=True)).first()
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

    # ── A3 containment review (Opus reads; Astra's pure functions decide) ──
    def load_spc_investigation_inputs(self, db: Session, target_inspection_id: str, principal: Principal):
        """The chart context of the target inspection and the committed inspection prefix for that part/station/method,
        in inspection record order. Every field is read from stored records; nothing is inferred."""
        from app.features.spc.service import InvestigationContext, InvestigationObservation
        target = db.get(m.Inspection, target_inspection_id)
        if not target:
            raise ApiError(404, "INSPECTION_NOT_FOUND", "Inspection not found")
        prof = db.get(m.InspectionProfile, target.profile_id) if target.profile_id else None
        if not prof or not prof.reference_sha256:
            raise ApiError(409, "SPC_CONTEXT_UNAVAILABLE", "The target inspection has no calibrated reference identity to investigate against")
        part_id = db.get(m.Unit, target.unit_id).part_id
        ctx = InvestigationContext(part_id=part_id, station_id=target.station_id, method_version=target.method_version,
                                   profile_id=prof.id, reference_sha256=prof.reference_sha256)
        rows = db.scalars(select(m.Inspection).join(m.Unit, m.Unit.id == m.Inspection.unit_id)
                          .where(m.Unit.part_id == part_id, m.Inspection.station_id == target.station_id,
                                 m.Inspection.method_version == target.method_version,
                                 (m.Inspection.created_at < target.created_at) | ((m.Inspection.created_at == target.created_at) & (m.Inspection.id <= target.id)))
                          .order_by(m.Inspection.created_at, m.Inspection.id)).all()
        unit_ids = {r.unit_id for r in rows}
        # A unit's routine inspection is its first one anywhere; any later one is a reinspection.
        first = {}
        for uid, iid in db.execute(select(m.Inspection.unit_id, m.Inspection.id).where(m.Inspection.unit_id.in_(unit_ids))
                                   .order_by(m.Inspection.created_at, m.Inspection.id)):
            first.setdefault(uid, iid)
        profiles = {pr.id: pr for pr in db.scalars(select(m.InspectionProfile).where(m.InspectionProfile.id.in_({r.profile_id for r in rows if r.profile_id})))}
        live = set(db.scalars(select(m.Evidence.sha256).where(m.Evidence.sha256.in_({r.image_sha256 for r in rows}),
                                                               m.Evidence.evidence_source == "LIVE_CAPTURE")))
        events = []
        for r in rows:
            pr = profiles.get(r.profile_id)
            src = r.evidence_source if r.evidence_source in ("LIVE_CAPTURE", "PRERECORDED_EVAL", "SYNTHETIC_TEST") else None
            events.append(InvestigationObservation(
                observation_id=f"obs:{r.id}", inspection_id=r.id, unit_id=r.unit_id, t=r.created_at, score=r.raw_score,
                profile_id=r.profile_id, reference_sha256=pr.reference_sha256 if pr else None, calibrated=bool((r.analysis or {}).get("calibrated")),
                evidence_source=src, provenance_verified=src == "LIVE_CAPTURE" and r.image_sha256 in live, routine=first.get(r.unit_id) == r.id))
        _, _, rule, _ = spc_inputs(db, part_id, target.station_id, target.method_version)
        return ctx, events, rule

    def load_spc_containment_genealogy(self, db: Session, investigation, principal: Principal):
        """Bounded, recorded genealogy for the chart's part: lots (with recorded part and line), unit→lot rows, and the
        chart line's 4M records as leads. No exposure links are asserted: none are recorded as independent evidence."""
        from app.features.spc.service import ContainmentGenealogy, FourMChange, LotGenealogy, UnitGenealogy
        ctx = investigation.context
        line = db.scalars(select(m.Line).where(m.Line.station_id == ctx.station_id).order_by(m.Line.id)).first()
        lots = db.scalars(select(m.Lot).where(m.Lot.part_id == ctx.part_id).order_by(m.Lot.id)).all()
        units = db.execute(select(m.Unit.id, m.Unit.lot_id).where(m.Unit.lot_id.in_([lt.id for lt in lots])).order_by(m.Unit.id)).all()
        fm = db.scalars(select(m.FourMEvent).where(m.FourMEvent.line_id == line.id).order_by(m.FourMEvent.effective_at)).all() if line else []
        return ContainmentGenealogy(
            line_id=line.id if line else None,
            lots=tuple(LotGenealogy(lot_id=lt.id, part_id=lt.part_id, line_id=lt.line_id) for lt in lots),
            units=tuple(UnitGenealogy(unit_id=u, lot_id=lt) for u, lt in units),
            four_m=tuple(FourMChange(event_id=f.id, line_id=f.line_id, effective_at=f.effective_at, category=f.category) for f in fm))

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
