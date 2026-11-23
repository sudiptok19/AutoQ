"""Orders, packages, membership history, seal revisions and PKG v2 identity (CONTRACT §4.5, §10.3, §10.5). Owner: Opus.

- A unit can be packed only if it is dispatch-eligible right now and matches the order's part; one OPEN membership per
  unit is enforced by a partial unique index; removals close the interval (history is kept).
- Sealing enrolls a reference photo as a new seal revision and signs a PKG v2 token binding order, exact item
  passports, reference digest and revision id. Unpack invalidates the revision and the token.
- A seal recheck is committed only against the revision it was computed for (409 SEAL_REVISION_CHANGED otherwise).
Lock order: unit → package → order.
"""
from datetime import UTC, datetime

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.core import audit, outbox
from app.core.auth import Principal
from app.core.errors import ApiError
from app.core.ids import new_id
from app.core.signing import load_signing_key, register_active_key
from app.core.storage import get_evidence, put_evidence, sniff
from app.db import models as m
from app.db.locking import lock_set
from app.features.alerts.service import raise_alert
from app.features.passport_crypto.service import CryptoError, sign
from app.policy import eligibility, policies
from app.ports import SealContext, SealProfile

SOURCES = {"LIVE_CAPTURE", "PRERECORDED_EVAL", "SYNTHETIC_TEST"}
LOADABLE_STATES = {"SEALED", "SEAL_REVIEW"}


def _iso(dt):
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z") if dt else None


def ev_url(sha):
    return f"/api/v1/evidence/{sha}" if sha else None


# ── orders ─────────────────────────────────────────────────────────────────
def order_view(db: Session, o: m.Order) -> dict:
    return {"id": o.id, "order_no": o.order_no, "customer_id": o.customer_id, "part_id": o.part_id, "qty_units": o.qty_units, "destination": o.destination,
            "created_at": _iso(o.created_at)}


def create_order(db: Session, p: Principal, order_no: str, customer_id: str, part_id: str, qty_units: int, destination: str) -> dict:
    if not db.get(m.Customer, customer_id):
        raise ApiError(404, "CUSTOMER_NOT_FOUND", "Customer not found")
    if not db.get(m.Part, part_id):
        raise ApiError(404, "PART_NOT_FOUND", "Part not found")
    if db.scalars(select(m.Order).where(m.Order.order_no == order_no)).first():
        raise ApiError(409, "ORDER_EXISTS", f"Order {order_no} exists")
    o = m.Order(id=new_id("ord"), order_no=order_no, customer_id=customer_id, part_id=part_id, qty_units=qty_units, destination=destination)
    db.add(o); db.flush()
    audit.append(db, p.username, "ORDER_CREATED", "order", o.id, data={"order_no": order_no, "qty": qty_units})
    return order_view(db, o)


# ── packages ───────────────────────────────────────────────────────────────
def find_package(db: Session, key: str) -> m.Package:
    pkg = db.get(m.Package, key) or db.scalars(select(m.Package).where(m.Package.label == key)).first()
    if not pkg:
        raise ApiError(404, "PACKAGE_NOT_FOUND", "Package not found", {"key": key})
    return pkg


def open_items(db: Session, package_id: str) -> list[m.PackageItem]:
    return list(db.scalars(select(m.PackageItem).where(m.PackageItem.package_id == package_id, m.PackageItem.removed_at.is_(None)).order_by(m.PackageItem.added_at, m.PackageItem.id)))


def check_view(db: Session, c: m.SealCheck | None) -> dict | None:
    if not c:
        return None
    rev = db.get(m.SealRevision, c.revision_id)
    return {"id": c.id, "package_id": c.package_id, "revision_id": c.revision_id, "revision_no": rev.revision_no if rev else None, "kind": c.kind,
            "result": c.result, "method_version": c.method_version, "metrics": c.metrics, "quality_gate": c.quality_gate, "evidence": c.evidence,
            "evidence_source": c.evidence_source, "synthetic": c.evidence_source != "LIVE_CAPTURE", "explanation": c.explanation,
            "created_by": db.get(m.User, c.actor_id).username, "created_at": _iso(c.created_at)}


def last_check(db: Session, pkg: m.Package) -> m.SealCheck | None:
    if not pkg.current_seal_revision_id:
        return None
    return db.scalars(select(m.SealCheck).where(m.SealCheck.package_id == pkg.id, m.SealCheck.revision_id == pkg.current_seal_revision_id)
                      .order_by(m.SealCheck.created_at.desc(), m.SealCheck.id.desc()).limit(1)).first()


def active_load(db: Session, package_id: str) -> m.Load | None:
    return db.scalars(select(m.Load).where(m.Load.package_id == package_id, m.Load.removed_at.is_(None))).first()


def package_view(db: Session, pkg: m.Package, *, with_units: bool = True) -> dict:
    from app.features.catalog_sampling.service import unit_view
    items = open_items(db, pkg.id)
    units = [unit_view(db, db.get(m.Unit, i.unit_id)) for i in items] if with_units else []
    blockers = [b for u in units for b in u["eligibility"]["blockers"]]
    held = [b for b in blockers if b["code"] in ("QUARANTINED", "CONTAINMENT_ACTIVE", "PASSPORT_HELD", "PASSPORT_REVOKED")]
    rev = db.get(m.SealRevision, pkg.current_seal_revision_id) if pkg.current_seal_revision_id else None
    load = active_load(db, pkg.id)
    return {"id": pkg.id, "label": pkg.label, "order_id": pkg.order_id, "part_id": pkg.part_id, "state": pkg.state, "unit_ids": [i.unit_id for i in items],
            "units": units, "held": bool(held), "hold_reasons": held, "token": pkg.token,
            "seal_revision": {"id": rev.id, "revision_no": rev.revision_no, "ref_sha256": rev.ref_sha256, "reference_url": ev_url(rev.ref_sha256),
                              "enrolled_at": _iso(rev.enrolled_at)} if rev else None,
            "seal_ref_sha256": rev.ref_sha256 if rev else None, "last_seal_check": check_view(db, last_check(db, pkg)),
            "shipment_id": load.shipment_id if load else None, "created_at": _iso(pkg.created_at)}


def create_package(db: Session, p: Principal, order_id: str) -> dict:
    [order] = lock_set(db).lock(m.Order, [order_id]) or [None]
    if not order:
        raise ApiError(404, "ORDER_NOT_FOUND", "Order not found")
    n = db.scalar(text("SELECT nextval('package_label_seq')"))
    pkg = m.Package(id=new_id("pkg"), label=f"PKG-{n:04d}", order_id=order.id, part_id=order.part_id, state="OPEN", created_by=p.id)
    db.add(pkg); db.flush()
    audit.append(db, p.username, "PACKAGE_CREATED", "package", pkg.id, data={"order": order.order_no, "label": pkg.label})
    return package_view(db, pkg)


def add_item(db: Session, p: Principal, package_key: str, unit_key: str | None, token: str | None) -> dict:
    from app.features.inspection_passports.passports import verify_token
    pkg0 = find_package(db, package_key)
    if token:
        v = verify_token(db, token)
        if not v["signature_valid"] or v["typ"] != "PP" or not v["exists"]:
            raise ApiError(409, "TOKEN_INVALID", "Passport token not valid for packing", {"failure": v.get("failure") or v.get("status")})
        unit_key = v["payload"]["unit"]
    unit0 = db.get(m.Unit, unit_key or "") or db.scalars(select(m.Unit).where(m.Unit.serial == (unit_key or ""))).first()
    if not unit0:
        raise ApiError(404, "UNIT_NOT_FOUND", "Unit not found")
    locks = lock_set(db)
    [unit] = locks.lock(m.Unit, [unit0.id])
    [pkg] = locks.lock(m.Package, [pkg0.id])
    if pkg.state != "OPEN":
        raise ApiError(409, "PACKAGE_NOT_OPEN", f"Package {pkg.label} is {pkg.state}")
    blockers = list(eligibility.compute(db, unit).blockers)
    if unit.part_id != pkg.part_id:
        blockers.append({"code": "WRONG_PART", "message": "Unit part does not match the package's order"})
    current = db.scalars(select(m.PackageItem).where(m.PackageItem.unit_id == unit.id, m.PackageItem.removed_at.is_(None))).first()
    if current:
        blockers.append({"code": "ALREADY_PACKED", "message": f"Unit is already in {db.get(m.Package, current.package_id).label}"})
    if blockers:
        raise ApiError(409, "UNIT_NOT_PACKABLE", f"{unit.serial} cannot be packed", {"blockers": blockers})
    pp = eligibility.current_passport(db, unit)
    db.add(m.PackageItem(id=new_id("pki"), package_id=pkg.id, unit_id=unit.id, passport_id=pp.id, added_by=p.id))
    db.flush()
    audit.append(db, p.username, "UNIT_PACKED", "package", pkg.id, data={"unit": unit.serial, "passport": pp.id})
    return package_view(db, pkg)


def remove_item(db: Session, p: Principal, package_key: str, unit_id: str, reason: str | None) -> dict:
    pkg0 = find_package(db, package_key)
    locks = lock_set(db)
    locks.lock(m.Unit, [unit_id])
    [pkg] = locks.lock(m.Package, [pkg0.id])
    if pkg.state != "OPEN":
        raise ApiError(409, "PACKAGE_NOT_OPEN", "Unpack the package before removing units")
    item = db.scalars(select(m.PackageItem).where(m.PackageItem.package_id == pkg.id, m.PackageItem.unit_id == unit_id, m.PackageItem.removed_at.is_(None))).first()
    if not item:
        raise ApiError(404, "ITEM_NOT_FOUND", "Unit is not in this package")
    item.removed_at, item.removed_by, item.removal_reason = datetime.now(UTC), p.id, reason
    db.flush()
    audit.append(db, p.username, "UNIT_UNPACKED", "package", pkg.id, reason, data={"unit": unit_id})
    return package_view(db, pkg)


# ── seals ──────────────────────────────────────────────────────────────────
def seal_profile(db: Session, method_version: str) -> SealProfile:
    pol = policies.settings_of(policies.active(db))
    return SealProfile(method_version=method_version, blur_min=pol.inspection.blur_min, glare_max=pol.inspection.glare_max)


def _seal_impl():
    from app.features.seal_vision import service as impl  # Astra A2
    return impl


def _sign_pkg(db: Session, pkg: m.Package, rev: m.SealRevision) -> str:
    kid, key = load_signing_key()
    register_active_key(db)
    items = sorted(i.passport_id for i in open_items(db, pkg.id))
    payload = {"v": 2, "typ": "PKG", "kid": kid, "id": pkg.id, "iat": int(datetime.now(UTC).timestamp()), "label": pkg.label, "order": pkg.order_id,
               "items": items, "seal": rev.ref_sha256, "seal_revision_id": rev.id}
    try:
        return sign(payload, kid, key)
    except CryptoError as e:
        raise ApiError(500, e.code, f"Package signing failed: {e.message}", {"component": "passport_crypto"})


def _enroll(db: Session, p: Principal, pkg: m.Package, image: bytes, evidence_source: str, reason: str | None, kind: str) -> m.SealRevision:
    impl = _seal_impl()
    profile = seal_profile(db, impl.METHOD_VERSION)
    ref = impl.assess_reference(image, profile)
    if not ref.usable:
        raise ApiError(422, "SEAL_REFERENCE_UNUSABLE", f"Reference photo rejected: {', '.join(ref.reason_codes)}", {"quality_gate": ref.quality_gate, "keypoints": ref.keypoints})
    media = sniff(image)
    sha = put_evidence(db, image, media, evidence_source, p.id)
    now = datetime.now(UTC)
    for old in db.scalars(select(m.SealRevision).where(m.SealRevision.package_id == pkg.id, m.SealRevision.invalidated_at.is_(None))):
        old.invalidated_at, old.invalidation_reason = now, reason or "superseded by re-enrolment"
    n = (db.scalar(select(func.max(m.SealRevision.revision_no)).where(m.SealRevision.package_id == pkg.id)) or 0) + 1
    rev = m.SealRevision(id=new_id("srev"), package_id=pkg.id, revision_no=n, ref_sha256=sha, reference_features={"keypoints": ref.keypoints, "method_version": impl.METHOD_VERSION}, enrolled_by=p.id)
    db.add(rev); db.flush()
    db.add(m.SealCheck(id=new_id("seal"), package_id=pkg.id, revision_id=rev.id, kind="ENROLL", result=None, method_version=impl.METHOD_VERSION,
                       metrics={"keypoints_ref": ref.keypoints}, quality_gate=ref.quality_gate, evidence={"reference_url": ev_url(sha)},
                       explanation=f"Reference enrolled (revision {n}).", evidence_source=evidence_source, actor_id=p.id))
    pkg.current_seal_revision_id = rev.id
    pkg.token = _sign_pkg(db, pkg, rev)
    pkg.state = "SEALED"
    db.flush()
    audit.append(db, p.username, f"SEAL_{kind}", "package", pkg.id, reason, data={"revision": n, "ref": sha, "keypoints": ref.keypoints, "source": evidence_source})
    return rev


def seal(db: Session, p: Principal, package_key: str, image: bytes, evidence_source: str) -> dict:
    if evidence_source not in SOURCES:
        raise ApiError(422, "EVIDENCE_SOURCE_INVALID", f"evidence_source must be one of {sorted(SOURCES)}")
    pkg0 = find_package(db, package_key)
    items = open_items(db, pkg0.id)
    locks = lock_set(db)
    units = locks.lock(m.Unit, [i.unit_id for i in items])
    [pkg] = locks.lock(m.Package, [pkg0.id])
    if pkg.state != "OPEN":
        raise ApiError(409, "PACKAGE_NOT_OPEN", f"Package {pkg.label} is {pkg.state}")
    if not items:
        raise ApiError(409, "PACKAGE_EMPTY", "Cannot seal an empty package")
    bad = [{"serial": u.serial, "blockers": eligibility.compute(db, u).blockers} for u in units if not eligibility.compute(db, u).dispatch_eligible]
    if bad:
        raise ApiError(409, "CONTENTS_NOT_ELIGIBLE", "Remove ineligible units before sealing", {"units": bad})
    current = {u.id: eligibility.current_passport(db, u) for u in units}
    stale = [i.unit_id for i in items if not current[i.unit_id] or current[i.unit_id].id != i.passport_id]
    if stale:
        raise ApiError(409, "PASSPORT_CHANGED", "A packed unit's passport changed since packing; repack it", {"unit_ids": stale})
    _enroll(db, p, pkg, image, evidence_source, None, "ENROLLED")
    return package_view(db, pkg)


def unpack(db: Session, p: Principal, package_key: str, reason: str) -> dict:
    pkg0 = find_package(db, package_key)
    locks = lock_set(db)
    locks.lock(m.Unit, [i.unit_id for i in open_items(db, pkg0.id)])
    [pkg] = locks.lock(m.Package, [pkg0.id])
    if pkg.state not in LOADABLE_STATES:
        raise ApiError(409, "PACKAGE_NOT_SEALED" if pkg.state == "OPEN" else "PACKAGE_MOVED", f"Package is {pkg.state}")
    now = datetime.now(UTC)
    for rev in db.scalars(select(m.SealRevision).where(m.SealRevision.package_id == pkg.id, m.SealRevision.invalidated_at.is_(None))):
        rev.invalidated_at, rev.invalidation_reason = now, f"unpacked: {reason}"
    pkg.state, pkg.token, pkg.current_seal_revision_id = "OPEN", None, None
    db.flush()
    audit.append(db, p.username, "PACKAGE_UNPACKED", "package", pkg.id, reason)
    return package_view(db, pkg)


def re_enroll(db: Session, p: Principal, package_key: str, image: bytes, reason: str, evidence_source: str) -> dict:
    pkg0 = find_package(db, package_key)
    locks = lock_set(db)
    locks.lock(m.Unit, [i.unit_id for i in open_items(db, pkg0.id)])
    [pkg] = locks.lock(m.Package, [pkg0.id])
    if pkg.state != "SEAL_REVIEW":
        raise ApiError(409, "NOT_IN_SEAL_REVIEW", "Re-enrolment is only for packages in SEAL_REVIEW (after a suspicious recheck)")
    _enroll(db, p, pkg, image, evidence_source, reason, "RE_ENROLLED")
    return package_view(db, pkg)


# ── port implementations used by the seal-check route (Astra's A2 or interim) ──
def load_seal_context(db: Session, package_id: str) -> SealContext:
    pkg = find_package(db, package_id)
    if not pkg.current_seal_revision_id:
        raise ApiError(409, "SEAL_NOT_ENROLLED", f"Package {pkg.label} has no active seal reference")
    rev = db.get(m.SealRevision, pkg.current_seal_revision_id)
    method = (rev.reference_features or {}).get("method_version") or _seal_impl().METHOD_VERSION
    return SealContext(package_id=pkg.id, revision_id=rev.id, reference_bytes=get_evidence(db, rev.ref_sha256)[0], profile=seal_profile(db, method))


def record_recheck(db: Session, ctx: SealContext, ev, probe: bytes, media_type: str, evidence_source: str, p) -> dict:
    if evidence_source not in SOURCES:
        raise ApiError(422, "EVIDENCE_SOURCE_INVALID", f"evidence_source must be one of {sorted(SOURCES)}")
    pkg0 = db.get(m.Package, ctx.package_id)
    locks = lock_set(db)
    locks.lock(m.Unit, [i.unit_id for i in open_items(db, pkg0.id)])
    [pkg] = locks.lock(m.Package, [ctx.package_id])
    if pkg.current_seal_revision_id != ctx.revision_id:
        raise ApiError(409, "SEAL_REVISION_CHANGED", "The package was resealed or unpacked during analysis; recapture", {"expected": ctx.revision_id, "current": pkg.current_seal_revision_id})
    probe_sha = put_evidence(db, probe, sniff(probe) or media_type, evidence_source, p.id)
    aligned = put_evidence(db, ev.aligned_png, "image/png", evidence_source, p.id) if ev.aligned_png else None
    diff = put_evidence(db, ev.diff_png, "image/png", evidence_source, p.id) if ev.diff_png else None
    rev = db.get(m.SealRevision, ctx.revision_id)
    chk = m.SealCheck(id=new_id("seal"), package_id=pkg.id, revision_id=rev.id, kind="RECHECK", result=ev.result, method_version=ev.method_version,
                      metrics=ev.metrics, quality_gate=ev.quality_gate,
                      evidence={"reference_url": ev_url(rev.ref_sha256), "probe_url": ev_url(probe_sha), "aligned_url": ev_url(aligned), "diff_url": ev_url(diff)},
                      explanation=ev.explanation, evidence_source=evidence_source, actor_id=p.id)
    db.add(chk)
    if ev.result == "SUSPICIOUS" and pkg.state == "SEALED":
        pkg.state = "SEAL_REVIEW"
    db.flush()
    audit.append(db, p.username, "SEAL_RECHECK", "package", pkg.id, data={"result": ev.result, "revision": rev.revision_no, "ssim": ev.metrics.get("ssim"), "source": evidence_source})
    if ev.result == "SUSPICIOUS":
        raise_alert(db, type="SEAL_TAMPER", severity="CRITICAL", entity_type="PACKAGE", entity_id=pkg.id, reason_codes=["SEAL_SUSPICIOUS"],
                    details={"package": pkg.label, "seal_check_id": chk.id, "revision_id": rev.id, "ssim": ev.metrics.get("ssim")},
                    dedupe_key=f"seal:{chk.id}", audible=True)
        outbox.emit(db, "DISPATCH_STATE_CHANGED", "PACKAGE", pkg.id, severity="HIGH", reason_codes=["SEAL_SUSPICIOUS"], payload={"state": pkg.state, "label": pkg.label})
    return check_view(db, chk)
