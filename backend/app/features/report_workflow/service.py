"""Evidence-linked 8D/SCAR drafts (CONTRACT §10.5). Owner: Opus; the PDF renderer is Astra's A5.

A report revision is a FROZEN snapshot (facts, counts, evidence refs, as-of time) hashed and stored; later state
changes never alter it. Items are tagged FACT / HYPOTHESIS / PROPOSED / PENDING_HUMAN. Root cause (D4),
verification (D6) and closure (D8) stay PENDING_HUMAN until supported by evidence and approved by a QM.
Deterministic template generator — no LLM invents a root cause, an action or an approval.
"""
import hashlib
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core import audit
from app.core.auth import Principal
from app.core.canonical import canonicalize
from app.core.errors import ApiError
from app.core.ids import new_id
from app.core.storage import put_evidence
from app.db import models as m
from app.ports import EvidenceRef, ReportItem, ReportSection, ReportSnapshot

GENERATOR = "template-8d-0.1"
TITLES = {"D1": "Team", "D2": "Problem description", "D3": "Interim containment", "D4": "Root cause", "D5": "Permanent corrective action",
          "D6": "Verification of effectiveness", "D7": "Prevent recurrence", "D8": "Closure and recognition"}
_ASCII = str.maketrans({"—": "-", "–": "-", "→": "->", "≥": ">=", "≤": "<=", "·": "-", "’": "'", "“": '"', "”": '"'})


def _t(s: str) -> str:
    return s.translate(_ASCII)


def _iso(dt):
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z") if dt else None


def item(kind: str, text: str, *refs: dict) -> dict:
    return {"kind": kind, "text": _t(text), "evidence": [r for r in refs if r]}


def ref(type_: str, id_: str, label: str, sha256: str | None = None) -> dict:
    return {"type": type_, "id": id_, "label": _t(label), "sha256": sha256}


def _locations(db: Session, unit_ids: list[str]) -> dict:
    out = {"at_plant": 0, "loaded_not_departed": 0, "in_transit": 0, "delivered": 0}
    held_pkgs = set()
    for uid in unit_ids:
        row = db.execute(select(m.PackageItem.package_id, m.Shipment.status).select_from(m.PackageItem)
                         .join(m.Load, (m.Load.package_id == m.PackageItem.package_id) & m.Load.removed_at.is_(None), isouter=True)
                         .join(m.Shipment, m.Shipment.id == m.Load.shipment_id, isouter=True)
                         .where(m.PackageItem.unit_id == uid, m.PackageItem.removed_at.is_(None))).first()
        status = row[1] if row else None
        if status in ("OPEN", "CLOSED"):
            out["loaded_not_departed"] += 1
        elif status == "IN_TRANSIT":
            out["in_transit"] += 1
        elif status == "DELIVERED":
            out["delivered"] += 1
        else:
            out["at_plant"] += 1
        if row and status not in ("IN_TRANSIT", "DELIVERED"):
            held_pkgs.add(row[0])
    return out | {"held_packages": len(held_pkgs)}


def _evidence_source(db: Session, unit_ids: list[str], since: datetime | None) -> str:
    q = select(m.Inspection.evidence_source).where(m.Inspection.unit_id.in_(unit_ids)).distinct()
    if since:
        q = q.where(m.Inspection.created_at >= since)
    srcs = set(db.scalars(q))
    return srcs.pop() if len(srcs) == 1 else "MIXED" if srcs else "LIVE_CAPTURE"


def _build_four_m(db: Session, ev: m.FourMEvent) -> tuple[dict, dict, list[dict]]:
    run = db.scalars(select(m.ContainmentRun).where(m.ContainmentRun.event_id == ev.id)).first()
    line = db.get(m.Line, ev.line_id)
    actor = db.get(m.User, ev.actor_id)
    unit_ids = list(db.scalars(select(m.ContainmentUnit.unit_id).where(m.ContainmentUnit.run_id == run.id))) if run else []
    insp = list(db.scalars(select(m.Inspection).where(m.Inspection.unit_id.in_(unit_ids), m.Inspection.created_at >= ev.created_at).order_by(m.Inspection.created_at)))
    verdicts = {v: sum(i.verdict == v for i in insp) for v in ("PASS", "HOLD", "REJECT")}
    rejects = [i for i in insp if i.verdict == "REJECT"]
    tasks = list(db.scalars(select(m.SegregationTask).join(m.Package, m.Package.id == m.SegregationTask.package_id)
                            .join(m.PackageItem, m.PackageItem.package_id == m.Package.id).where(m.PackageItem.unit_id.in_(unit_ids)).distinct()))
    ev_ref = ref("FOUR_M", ev.id, f"{ev.code} {ev.category} on {line.name}")
    run_ref = ref("CONTAINMENT", run.id, f"Containment run {run.id}") if run else None
    rej_refs = [ref("INSPECTION", i.id, f"REJECT {db.get(m.Unit, i.unit_id).serial} score {i.raw_score}", i.image_sha256) for i in rejects[:20]]
    loc = _locations(db, unit_ids)
    counts = {"affected_units": len(unit_ids), "held_packages": loc.pop("held_packages"), **loc}
    cleared = run and run.status == "CLEARED"
    sections = {
        "D1": [item("FACT", f"Change point logged by {actor.display_name} ({actor.username}).", ev_ref),
               item("PENDING_HUMAN", "Name the cross-functional team (quality, production, maintenance, supplier quality).")],
        "D2": [item("FACT", f"{'Planned' if ev.planned else 'Unplanned'} {ev.category} change on {line.name}: {ev.description}", ev_ref),
               item("FACT", f"{len(unit_ids)} serialized units scoped under policy {ev.policy_version}; post-change inspections: "
                            f"{verdicts['PASS']} PASS, {verdicts['HOLD']} HOLD, {verdicts['REJECT']} REJECT.", *( [run_ref] if run_ref else [])),
               *([item("FACT", f"{len(rejects)} rejected unit(s) after the change.", *rej_refs)] if rejects else [])],
        "D3": [item("FACT", f"Containment run status {run.status}: {run.cleared_qty}/{run.required_qty} distinct passing units in epoch {run.sequence_epoch}; "
                            "dispatch of scoped units blocked and passports held until clearance.", run_ref) if run else item("PENDING_HUMAN", "No containment run found."),
               *([item("FACT", f"{len(tasks)} segregation task(s) raised for packages containing scoped units.",
                       *[ref("TASK", t.id, f"{t.type} {t.status}") for t in tasks[:20]])] if tasks else [])],
        "D4": [item("HYPOTHESIS", f"Reported reason at logging: {ev.reason}", ev_ref),
               item("PENDING_HUMAN", "Root cause not yet verified. Attach evidence (e.g. tool wear measurement, operator qualification record).")],
        "D5": [item("PROPOSED", f"Re-validate the {ev.category.lower()} change with first-off approval before releasing further production."),
               item("PENDING_HUMAN", "Define and own the permanent corrective action.")],
        "D6": ([item("FACT", f"Containment cleared by QM after {run.cleared_qty} distinct passing units: {run.clearance_reason}", run_ref)] if cleared else [])
              + [item("PENDING_HUMAN", "Effectiveness of the permanent action not yet verified.")],
        "D7": [item("PROPOSED", "Add the change to the 4M change-point register with mandatory pre-notification."),
               item("PENDING_HUMAN", "Update PFMEA / control plan and confirm the owner.")],
        "D8": [item("PENDING_HUMAN", "Closure requires quality-manager approval with review evidence.")],
    }
    meta = {"source_label": f"{ev.code} {ev.category}", "line": line.name, "as_of_policy": ev.policy_version}
    return sections, counts, [meta, _evidence_source(db, unit_ids, ev.created_at)]


def _build_alert(db: Session, a: m.Alert) -> tuple[dict, dict, list]:
    aref = ref("ALERT", a.id, f"{a.type} {a.severity}")
    sections = {
        "D1": [item("PENDING_HUMAN", "Name the team responding to this alert.")],
        "D2": [item("FACT", f"Alert {a.type} ({a.severity}) on {a.entity_type.lower()} {a.entity_id}: reasons {', '.join(a.reason_codes) or 'none'}.", aref)],
        "D3": [item("FACT", f"Alert status {a.status}." + (f" Acknowledged: {a.ack_reason}" if a.ack_reason else ""), aref)],
        "D4": [item("PENDING_HUMAN", "Root cause not yet investigated.")],
        "D5": [item("PENDING_HUMAN", "Define the corrective action.")],
        "D6": [item("PENDING_HUMAN", "Verification pending.")],
        "D7": [item("PENDING_HUMAN", "Recurrence prevention pending.")],
        "D8": [item("PENDING_HUMAN", "Closure requires quality-manager approval with review evidence.")],
    }
    counts = {"affected_units": 0, "held_packages": 0, "at_plant": 0, "loaded_not_departed": 0, "in_transit": 0, "delivered": 0}
    return sections, counts, [{"source_label": f"{a.type} {a.id}"}, "LIVE_CAPTURE"]


def _build_recall(db: Session, r: m.Recall) -> tuple[dict, dict, list]:
    imp = r.impact or {}
    uc = imp.get("unit_counts", {})
    counts = {"affected_units": int(imp.get("units", 0)), "held_packages": len(imp.get("held_package_ids", [])),
              **{k: int(uc.get(k, 0)) for k in ("at_plant", "loaded_not_departed", "in_transit", "delivered")}}
    rr = ref("RECALL", r.id, f"Recall of {r.scope_type.lower()} {imp.get('scope_label', r.scope_id)}")
    tasks = list(db.scalars(select(m.SegregationTask).where(m.SegregationTask.recall_id == r.id)))
    sections = {
        "D1": [item("PENDING_HUMAN", "Name the recall team and the supplier contact.")],
        "D2": [item("FACT", f"Quarantine of {r.scope_type.lower()} {imp.get('scope_label', r.scope_id)}: {r.reason}", rr),
               item("FACT", f"{counts['affected_units']} units affected: {counts['at_plant']} at plant, {counts['loaded_not_departed']} loaded, "
                            f"{counts['in_transit']} in transit, {counts['delivered']} delivered.", rr)],
        "D3": [item("FACT", f"{counts['held_packages']} package(s) held; {len(tasks)} physical action task(s) raised.",
                    *[ref("TASK", t.id, f"{t.type} {t.status}") for t in tasks[:20]])],
        "D4": [item("PENDING_HUMAN", "Root cause at the material/supplier level not yet verified.")],
        "D5": [item("PENDING_HUMAN", "Define the permanent corrective action with the supplier.")],
        "D6": [item("PENDING_HUMAN", "Verification pending.")],
        "D7": [item("PENDING_HUMAN", "Recurrence prevention pending.")],
        "D8": [item("PENDING_HUMAN", "Closure requires quality-manager approval with review evidence.")],
    }
    return sections, counts, [{"source_label": rr["label"]}, "LIVE_CAPTURE"]


def _snapshot_dict(report: m.Report, revision_id: str, sections: dict, counts: dict, meta: dict, evidence_source: str, as_of: datetime,
                   approved_by: str | None = None, approved_at: datetime | None = None) -> dict:
    return {"id": report.id, "revision_id": revision_id, "status": report.status, "source_type": report.source_type, "source_id": report.source_id,
            "generator": report.generator, "created_at": _iso(report.created_at), "as_of": _iso(as_of), "counts": counts,
            "evidence_source": evidence_source, "approved_by": approved_by, "approved_at": _iso(approved_at), "meta": meta,
            "sections": [{"key": k, "title": TITLES[k], "items": sections[k]} for k in TITLES]}


def _store_revision(db: Session, report: m.Report, snap: dict, actor_id: str) -> m.ReportRevision:
    rev = m.ReportRevision(id=snap["revision_id"], report_id=report.id, snapshot=snap, snapshot_sha256=hashlib.sha256(canonicalize(snap).encode()).hexdigest(),
                           created_by=actor_id)
    db.add(rev); db.flush()
    seen = set()
    for sec in snap["sections"]:
        for it in sec["items"]:
            for e in it["evidence"]:
                key = (e["type"], e["id"])
                if key not in seen:
                    seen.add(key)
                    db.add(m.ReportEvidence(revision_id=rev.id, entity_type=e["type"], entity_id=e["id"], sha256=e.get("sha256")))
    report.current_revision_id = rev.id
    db.flush()
    return rev


def create(db: Session, p: Principal, source_type: str, source_id: str) -> dict:
    if source_type == "FOUR_M":
        src = db.get(m.FourMEvent, source_id) or db.scalars(select(m.FourMEvent).where(m.FourMEvent.code == source_id)).first()
        builder = _build_four_m
    elif source_type == "ALERT":
        src, builder = db.get(m.Alert, source_id), _build_alert
    else:
        src, builder = db.get(m.Recall, source_id), _build_recall
    if not src:
        raise ApiError(404, "SOURCE_NOT_FOUND", f"{source_type} {source_id} not found")
    now = datetime.now(UTC)
    report = m.Report(id=new_id("rpt"), source_type=source_type, source_id=src.id, status="DRAFT", generator=GENERATOR, created_by=p.id, created_at=now)
    db.add(report); db.flush()
    sections, counts, (meta, evsrc) = builder(db, src)
    snap = _snapshot_dict(report, new_id("rrev"), sections, counts, meta, evsrc, now)
    rev = _store_revision(db, report, snap, p.id)
    audit.append(db, p.username, "REPORT_DRAFTED", "report", report.id, data={"source": f"{source_type}:{src.id}", "revision": rev.id, "sha256": rev.snapshot_sha256})
    return view(db, report)


def view(db: Session, r: m.Report) -> dict:
    rev = db.get(m.ReportRevision, r.current_revision_id)
    return rev.snapshot | {"snapshot_sha256": rev.snapshot_sha256, "pdf_url": f"/api/v1/reports/{r.id}/pdf",
                           "pdf_sha256": rev.pdf_sha256, "revisions": db.scalar(select(func.count()).select_from(m.ReportRevision).where(m.ReportRevision.report_id == r.id))}


def find(db: Session, report_id: str) -> m.Report:
    r = db.get(m.Report, report_id)
    if not r:
        raise ApiError(404, "REPORT_NOT_FOUND", "Report not found")
    return r


def approve(db: Session, p: Principal, report_id: str, reason: str, review_evidence_ids: list[str]) -> dict:
    r = db.scalars(select(m.Report).where(m.Report.id == report_id).with_for_update().execution_options(populate_existing=True)).first()
    if not r:
        raise ApiError(404, "REPORT_NOT_FOUND", "Report not found")
    if r.status == "APPROVED":
        raise ApiError(409, "REPORT_APPROVED", "Report already approved")
    if not review_evidence_ids:
        raise ApiError(409, "REPORT_INCOMPLETE", "Approval requires review evidence (sha256 of attached review records)")
    missing = [s for s in review_evidence_ids if not db.get(m.Evidence, s)]
    if missing:
        raise ApiError(409, "REPORT_INCOMPLETE", "Review evidence not found in the evidence store", {"missing": missing})
    prev = db.get(m.ReportRevision, r.current_revision_id).snapshot
    now = datetime.now(UTC)
    r.status, r.approved_by, r.approved_at, r.approval_reason = "APPROVED", p.id, now, reason
    sections = {s["key"]: s["items"] for s in prev["sections"]}
    sections["D8"] = [item("FACT", f"Approved by {p.display_name} ({p.username}): {reason}", *[ref("EVIDENCE", s, "Review record", s) for s in review_evidence_ids])]
    snap = _snapshot_dict(r, new_id("rrev"), sections, prev["counts"], prev["meta"], prev["evidence_source"], datetime.fromisoformat(prev["as_of"].replace("Z", "+00:00")),
                          approved_by=p.username, approved_at=now)
    rev = _store_revision(db, r, snap, p.id)
    audit.append(db, p.username, "REPORT_APPROVED", "report", r.id, reason, data={"revision": rev.id, "sha256": rev.snapshot_sha256, "evidence": review_evidence_ids})
    return view(db, r)


# ── ports for Astra's A5 renderer ──────────────────────────────────────────
def _dt(s: str | None):
    return datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None


def load_snapshot(db: Session, report_id: str) -> ReportSnapshot:
    r = find(db, report_id)
    s = db.get(m.ReportRevision, r.current_revision_id).snapshot
    secs = tuple(ReportSection(key=x["key"], title=x["title"], items=tuple(
        ReportItem(kind=i["kind"], text=i["text"], evidence=tuple(EvidenceRef(type=e["type"], id=e["id"], label=e["label"], sha256=e.get("sha256")) for e in i["evidence"]))
        for i in x["items"])) for x in s["sections"])
    return ReportSnapshot(id=s["id"], revision_id=s["revision_id"], status=s["status"], source_type=s["source_type"], source_id=s["source_id"], generator=s["generator"],
                          created_at=_dt(s["created_at"]), as_of=_dt(s["as_of"]), counts=s["counts"], evidence_source=s["evidence_source"], sections=secs,
                          approved_by=s["approved_by"], approved_at=_dt(s["approved_at"]), meta=s.get("meta") or {})


def record_pdf(db: Session, snapshot: ReportSnapshot, pdf: bytes, p) -> str:
    rev = db.scalars(select(m.ReportRevision).where(m.ReportRevision.id == snapshot.revision_id).with_for_update().execution_options(populate_existing=True)).one()
    sha = put_evidence(db, pdf, "application/pdf", "SYNTHETIC_TEST" if snapshot.evidence_source == "SYNTHETIC_TEST" else "UPLOAD", p.id)
    if rev.pdf_sha256 and rev.pdf_sha256 != sha:
        raise ApiError(500, "REPORT_RENDER_NONDETERMINISTIC", "Re-rendering a frozen revision produced different bytes", {"component": "scar_pdf"})
    if not rev.pdf_sha256:
        rev.pdf_sha256 = sha
        db.flush()
        audit.append(db, p.username, "REPORT_PDF_RENDERED", "report", snapshot.id, data={"revision": rev.id, "pdf_sha256": sha})
    return sha
