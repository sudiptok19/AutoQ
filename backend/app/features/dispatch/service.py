"""Shipments, the 11-gate dispatch interlock, manifest close, departure re-validation and delivery
(CONTRACT §5, §10.4). Owner: Opus.

Every scan — accepted or blocked — is persisted with its full gate checklist. A scan is idempotent on
client_event_id (same content → original decision, replay=true; different content → 409). Locks follow
unit → package → order → shipment, so concurrent loaders of one package serialize and exactly one wins
(plus a partial unique index on active loads as a database backstop).
"""
import hashlib
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import audit, outbox
from app.core.auth import Principal
from app.core.canonical import canonicalize
from app.core.errors import ApiError
from app.core.ids import new_id
from app.db import models as m
from app.db.locking import LockSet, lock_set
from app.features.alerts.service import raise_alert
from app.features.dispatch import segregation
from app.features.inspection_passports.passports import trusted_keys
from app.features.packaging.service import active_load, find_package, last_check, open_items
from app.features.passport_crypto.service import CryptoError, verify
from app.policy import eligibility, policies

GATES = ["SIGNATURE", "KEY", "IDENTITY", "LIFECYCLE", "CONTAINMENT", "QUARANTINE", "ORDER", "DUPLICATE", "SEAL", "QUANTITY", "SEQUENCE"]
SIG_CODES = {"TOKEN_MALFORMED", "SIG_INVALID", "SCHEMA_INVALID", "VERSION_UNSUPPORTED"}
KEY_CODES = {"KEY_UNKNOWN", "KEY_REVOKED", "KEY_EXPIRED"}


def _iso(dt):
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z") if dt else None


def _sha(obj) -> str:
    return hashlib.sha256(canonicalize(obj).encode()).hexdigest()


def content_sha(shipment_id: str, token: str) -> str:
    """Fingerprint of what a scan claims: which label, onto which shipment. Idempotency and provenance compare this."""
    return _sha({"shipment": shipment_id, "token": token})


# ── quantities ─────────────────────────────────────────────────────────────
def loaded_units(db: Session, shipment_id: str) -> int:
    return db.scalar(select(func.coalesce(func.sum(m.Load.unit_count), 0)).where(m.Load.shipment_id == shipment_id, m.Load.removed_at.is_(None)))


def order_committed_units(db: Session, order_id: str, exclude_shipment: str | None = None) -> int:
    q = (select(func.coalesce(func.sum(m.Load.unit_count), 0)).join(m.Shipment, m.Shipment.id == m.Load.shipment_id)
         .where(m.Shipment.order_id == order_id, m.Load.removed_at.is_(None)))
    if exclude_shipment:
        q = q.where(m.Shipment.id != exclude_shipment)
    return db.scalar(q)


def expected_units(db: Session, s: m.Shipment) -> int:
    """Remaining order capacity for this shipment after committed loads on the order's other shipments."""
    return db.get(m.Order, s.order_id).qty_units - order_committed_units(db, s.order_id, exclude_shipment=s.id)


# ── views ──────────────────────────────────────────────────────────────────
def scan_view(db: Session, sc: m.Scan, *, replay: bool = False) -> dict:
    s = db.get(m.Shipment, sc.shipment_id)
    pkg = db.get(m.Package, sc.package_id) if sc.package_id else None
    return {"scan_id": sc.id, "shipment_id": sc.shipment_id, "decision": sc.decision, "replay": replay, "package_id": sc.package_id,
            "package_label": pkg.label if pkg else None, "reasons": sc.reasons, "checks": sc.checks, "offline": sc.offline,
            "shipment_progress": {"loaded_units": loaded_units(db, s.id), "expected_units": expected_units(db, s),
                                  "loaded_packages": db.scalar(select(func.count()).select_from(m.Load).where(m.Load.shipment_id == s.id, m.Load.removed_at.is_(None)))},
            "client_event_id": sc.client_event_id, "device_id": sc.device_id, "decided_at": _iso(sc.decided_at), "captured_at": _iso(sc.captured_at),
            "decided_by": db.get(m.User, sc.actor_id).username}


def shipment_view(db: Session, s: m.Shipment, *, scans: bool = True) -> dict:
    loads = list(db.scalars(select(m.Load).where(m.Load.shipment_id == s.id, m.Load.removed_at.is_(None)).order_by(m.Load.loaded_at)))
    moves = list(db.scalars(select(m.MovementEvent).where(m.MovementEvent.shipment_id == s.id).order_by(m.MovementEvent.occurred_at)))
    out = {"id": s.id, "code": s.code, "order_id": s.order_id, "vehicle_no": s.vehicle_no, "dock": s.dock, "status": s.status, "manifest_sha256": s.manifest_sha256,
           "loaded_package_ids": [ld.package_id for ld in loads], "loaded_units": sum(ld.unit_count for ld in loads), "expected_units": expected_units(db, s),
           "movements": [{"type": mv.type, "location": mv.location, "occurred_at": _iso(mv.occurred_at), "recorded_at": _iso(mv.recorded_at)} for mv in moves],
           "closed_at": _iso(s.closed_at), "created_at": _iso(s.created_at)}
    from app.features.offline_scanner.service import outstanding_for_shipment
    out["offline_authorizations_open"] = outstanding_for_shipment(db, s.id)   # close and departure refuse while non-empty
    out["open_physical_tasks"] = [segregation.view(db, t) for t in open_physical_tasks(db, s.id)]
    if scans:
        out["scans"] = [scan_view(db, x) for x in db.scalars(select(m.Scan).where(m.Scan.shipment_id == s.id).order_by(m.Scan.decided_at.desc()).limit(200))]
    return out


def open_physical_tasks(db: Session, shipment_id: str) -> list[m.SegregationTask]:
    """Unload/segregate orders still open against this shipment, including crates that never made it onto the manifest
    (an offline-staged load the server refused). The truck may not leave until each has a recorded disposition."""
    return list(db.scalars(select(m.SegregationTask).where(m.SegregationTask.shipment_id == shipment_id, m.SegregationTask.status == "OPEN",
                                                           m.SegregationTask.type.in_(("REMOVAL_REQUIRED", "SEGREGATION_REQUIRED", "DEVICE_RECONCILIATION")))
                           .order_by(m.SegregationTask.created_at)))


def _reconciliation_blockers(db: Session, s: m.Shipment) -> list[dict]:
    """What still stands between this shipment and the road, beyond the gate itself. Caller holds the shipment lock,
    which snapshot issuance and settlement also take, so the answer cannot change underneath it."""
    from app.features.offline_scanner.service import outstanding_for_shipment
    out = []
    for o in outstanding_for_shipment(db, s.id):
        out.append({"code": "SNAPSHOT_UNSETTLED", "device_id": o["device_id"], "snapshot_id": o["snapshot_id"],
                    "message": f"Device {o['device_id']} ({o['operator']}) holds an offline authorization issued {o['issued_at']}"
                               + (" — recovery requested: complete its physical reconciliation task or attach reconciliation evidence"
                                  if o["recovery_requested_at"] else
                                  f"{' (expired, but its captures may still be on the phone)' if o['expired'] else ''} — end the offline session "
                                  "on the device, or a Quality Manager must recover it")})
    for t in open_physical_tasks(db, s.id):
        pkg = db.get(m.Package, t.package_id) if t.package_id else None
        out.append({"code": "PHYSICAL_TASK_OPEN", "task_id": t.id, "package_id": t.package_id,
                    "message": f"{t.type.replace('_', ' ').lower()} {pkg.label if pkg else '(unidentified crate)'} is still open — record what was done first"})
    return out


def find_shipment(db: Session, key: str) -> m.Shipment:
    s = db.get(m.Shipment, key) or db.scalars(select(m.Shipment).where(m.Shipment.code == key)).first()
    if not s:
        raise ApiError(404, "SHIPMENT_NOT_FOUND", "Shipment not found")
    return s


def create_shipment(db: Session, p: Principal, order_id: str, vehicle_no: str, dock: str) -> dict:
    o = db.get(m.Order, order_id)
    if not o:
        raise ApiError(404, "ORDER_NOT_FOUND", "Order not found")
    n = db.scalar(text("SELECT nextval('shipment_code_seq')"))
    s = m.Shipment(id=new_id("shp"), code=f"SHP-{n:04d}", order_id=o.id, vehicle_no=vehicle_no.strip(), dock=dock.strip(), status="OPEN")
    db.add(s); db.flush()
    audit.append(db, p.username, "SHIPMENT_CREATED", "shipment", s.id, data={"order": o.order_no, "vehicle": vehicle_no})
    return shipment_view(db, s)


# ── the gate ───────────────────────────────────────────────────────────────
class Gate:
    def __init__(self):
        self.checks: list[dict] = []
        self.reasons: list[dict] = []

    def ok(self, gate, detail):
        self.checks.append({"gate": gate, "status": "PASS", "detail": detail})

    def warn(self, gate, detail):
        self.checks.append({"gate": gate, "status": "WARN", "detail": detail})

    def fail(self, gate, code, detail):
        self.checks.append({"gate": gate, "status": "FAIL", "detail": detail})
        self.reasons.append({"code": code, "message": detail})

    def skip_rest(self):
        done = {c["gate"] for c in self.checks}
        self.checks += [{"gate": g, "status": "SKIP", "detail": "not evaluated — identity not established"} for g in GATES if g not in done]


def package_blockers(db: Session, pkg: m.Package, units: list[m.Unit]) -> tuple[list[dict], list[dict], list[dict]]:
    """(lifecycle, containment, quarantine) failures for a package's current contents."""
    life, cont, quar = [], [], []
    items = {i.unit_id: i for i in open_items(db, pkg.id)}
    for u in units:
        pp = eligibility.current_passport(db, u)
        if u.quality_status in ("REJECT", "HOLD"):
            life.append({"code": "UNIT_NOT_PASS", "message": f"{u.serial} is {u.quality_status}"})
        elif not pp:
            life.append({"code": "PASSPORT_REVOKED", "message": f"{u.serial} has no current passport"})
        elif pp.id != items[u.id].passport_id:
            life.append({"code": "PASSPORT_CHANGED", "message": f"{u.serial}'s passport changed since sealing — reseal"})
        for run in eligibility.open_containment_runs(db, u):
            cont.append({"code": "CONTAINMENT_ACTIVE", "message": f"{u.serial} under 4M containment {run.id}", "ref": run.id})
        for h in eligibility.active_holds(db, u):
            quar.append({"code": "QUARANTINED", "message": f"{u.serial}: recall hold on {h.entity_type.lower()}", "ref": h.recall_id})
    return life, cont, quar


def evaluate(db: Session, locks: LockSet, s0: m.Shipment, token: str, now: datetime) -> tuple[Gate, m.Package | None, list[m.Unit], m.Shipment]:
    g = Gate()
    pkg, units = None, []
    try:
        v = verify(token, trusted_keys(db), "PKG", int(now.timestamp()))
        g.ok("SIGNATURE", "Ed25519 · canonical AQ1 v2 payload")
        g.ok("KEY", f"kid {v.kid}")
    except CryptoError as e:
        if e.code in KEY_CODES:
            g.ok("SIGNATURE", "payload well-formed")
            g.fail("KEY", e.code, e.message)
        elif e.code == "TOKEN_MISMATCH":
            g.ok("SIGNATURE", "valid signature"); g.ok("KEY", "trusted")
            g.fail("IDENTITY", "TOKEN_MISMATCH", "Not a package label — scan the PKG QR")
        else:
            g.fail("SIGNATURE", e.code, e.message)
        g.skip_rest()
        return g, None, [], locks.lock(m.Shipment, [s0.id])[0]
    pkg0 = db.get(m.Package, v.payload["id"])
    if not pkg0:
        g.fail("IDENTITY", "PACKAGE_NOT_FOUND", "Signed package is unknown to this plant")
        g.skip_rest()
        return g, None, [], locks.lock(m.Shipment, [s0.id])[0]
    unit_ids = [i.unit_id for i in open_items(db, pkg0.id)]
    units = locks.lock(m.Unit, unit_ids)
    [pkg] = locks.lock(m.Package, [pkg0.id])
    locks.lock(m.Order, sorted({pkg.order_id, s0.order_id}))
    [s] = locks.lock(m.Shipment, [s0.id])
    if pkg.token != token:
        g.fail("IDENTITY", "TOKEN_MISMATCH", f"Label belongs to a superseded seal revision of {pkg.label}")
        g.skip_rest()
        return g, pkg, units, s
    g.ok("IDENTITY", f"{pkg.label} · {len(units)} passports · revision {v.payload['seal_revision_id']}")

    life, cont, quar = package_blockers(db, pkg, units)
    if pkg.state == "OPEN":
        g.fail("LIFECYCLE", "PACKAGE_NOT_SEALED", f"{pkg.label} is OPEN")
    elif pkg.state in ("IN_TRANSIT", "DELIVERED"):
        g.fail("LIFECYCLE", "PACKAGE_MOVED", f"{pkg.label} is {pkg.state}")
    elif life:
        g.fail("LIFECYCLE", life[0]["code"], "; ".join(x["message"] for x in life))
    else:
        g.ok("LIFECYCLE", f"{len(units)} units with current passports")
    g.fail("CONTAINMENT", "CONTAINMENT_ACTIVE", "; ".join(sorted({x["message"] for x in cont}))) if cont else g.ok("CONTAINMENT", "No open 4M containment")
    g.fail("QUARANTINE", "QUARANTINED", "; ".join(sorted({x["message"] for x in quar}))) if quar else g.ok("QUARANTINE", "No recall hold")
    order = db.get(m.Order, s.order_id)
    if s.status != "OPEN":
        g.fail("ORDER", "MANIFEST_CLOSED", f"Shipment {s.code} is {s.status}")
    elif pkg.order_id != s.order_id:
        g.fail("ORDER", "WRONG_ORDER", f"{pkg.label} is for {db.get(m.Order, pkg.order_id).order_no}, shipment is for {order.order_no}")
    else:
        g.ok("ORDER", order.order_no)
    load = active_load(db, pkg.id)
    if load:
        g.fail("DUPLICATE", "ALREADY_LOADED", f"Already loaded on {db.get(m.Shipment, load.shipment_id).code}")
    else:
        g.ok("DUPLICATE", "First accepted load")
    chk = last_check(db, pkg)
    max_age = policies.settings_of(policies.active(db)).seal_recheck_max_age_minutes
    if pkg.state == "SEAL_REVIEW" or (chk and chk.result == "SUSPICIOUS"):
        g.fail("SEAL", "SEAL_SUSPICIOUS", "Seal flagged suspicious — QE review required")
    elif not chk or chk.kind == "ENROLL":
        g.fail("SEAL", "SEAL_RECHECK_REQUIRED", f"Recheck the seal within {max_age} min before loading")
    elif chk.result == "UNVERIFIABLE":
        g.fail("SEAL", "SEAL_UNVERIFIABLE", "Last recheck unverifiable — recapture")
    elif now - chk.created_at > timedelta(minutes=max_age):
        g.fail("SEAL", "SEAL_RECHECK_REQUIRED", f"Last INTACT recheck older than {max_age} min")
    else:
        g.ok("SEAL", f"INTACT · {int((now - chk.created_at).total_seconds() // 60)} min ago · revision check {chk.id}")
    committed = order_committed_units(db, order.id)
    after = committed + len(units)
    g.fail("QUANTITY", "ORDER_QTY_EXCEEDED", f"{after} > {order.qty_units} units on {order.order_no}") if after > order.qty_units and not load else g.ok("QUANTITY", f"{after} / {order.qty_units} units")
    g.ok("SEQUENCE", "pack → seal → recheck → dock") if chk and chk.kind == "RECHECK" else g.warn("SEQUENCE", "No recheck in sequence")
    return g, pkg, units, s


def _replay(db: Session, p: Principal, prior: m.Scan, content: str, device_id: str | None, offline: bool) -> dict:
    """A scan already owns this client_event_id. Only the SAME request may replay it: same shipment+token, same actor,
    same device and same origin (online request vs offline sync). Anything else is a different capture that must not
    inherit the recorded decision, whichever transaction won the insert."""
    if prior.content_sha256 != content:
        raise ApiError(409, "EVENT_ID_REUSED", "client_event_id already used for a different scan", {"recorded_scan": prior.id})
    if prior.actor_id != p.id or prior.device_id != device_id or bool(prior.offline) != offline:
        raise ApiError(409, "EVENT_PROVENANCE_MISMATCH", "client_event_id belongs to a scan recorded by another request; it is not replayed",
                       {"recorded_scan": prior.id, "recorded_origin": "OFFLINE_SYNC" if prior.offline else "ONLINE"})
    return scan_view(db, prior, replay=True)


def scan(db: Session, p: Principal, shipment_key: str, token: str, client_event_id: str, device_id: str | None, *, offline: bool = False,
         captured_at: datetime | None = None, commit_load: bool = True,
         authorize: Callable[[m.Package | None], list[dict]] | None = None) -> tuple[dict, bool]:
    """Returns (scan view, created). Idempotent on client_event_id.
    `authorize` (offline replay) adds reasons from the capture's own authority — e.g. the signed snapshot's allowlist —
    evaluated against the package the gate resolved from the verified token. Any reason blocks the load."""
    s0 = find_shipment(db, shipment_key)
    content = content_sha(s0.id, token)
    prior = db.scalars(select(m.Scan).where(m.Scan.client_event_id == client_event_id)).first()
    if prior:
        return _replay(db, p, prior, content, device_id, offline), False
    now = datetime.now(UTC)
    locks = lock_set(db)
    g, pkg, units, s = evaluate(db, locks, s0, token, now)
    extra = authorize(pkg) if authorize else []
    reasons = g.reasons + extra
    accepted = not reasons and commit_load
    sc = m.Scan(id=new_id("scan"), shipment_id=s.id, package_id=pkg.id if pkg else None, client_event_id=client_event_id, content_sha256=content,
                device_id=device_id, decision="ACCEPTED" if accepted else "BLOCKED", reasons=reasons if reasons or commit_load else
                [{"code": "BLOCKED_ON_DEVICE", "message": "Device decided not to load"}], checks=g.checks, offline=offline, captured_at=captured_at,
                decided_at=now, actor_id=p.id)
    try:
        with db.begin_nested():
            db.add(sc)
            db.flush()
    except IntegrityError:  # a concurrent request with the same client_event_id won the insert
        prior = db.scalars(select(m.Scan).where(m.Scan.client_event_id == client_event_id)).one()
        return _replay(db, p, prior, content, device_id, offline), False
    if accepted:
        db.add(m.Load(id=new_id("load"), package_id=pkg.id, shipment_id=s.id, accepted_scan_id=sc.id, unit_count=len(units)))
        pkg.state = "LOADED"
        db.flush()
    codes = [r["code"] for r in sc.reasons]
    audit.append(db, p.username, "LOAD_ACCEPTED" if accepted else "LOAD_BLOCKED", "shipment", s.id, ",".join(codes) or None,
                 data={"package": pkg.label if pkg else None, "client_event_id": client_event_id, "offline": offline})
    outbox.emit(db, "DISPATCH_STATE_CHANGED", "SHIPMENT", s.id, severity="INFO" if accepted else "MEDIUM", reason_codes=codes,
                payload={"scan_id": sc.id, "decision": sc.decision, "package": pkg.label if pkg else None})
    alert_map = {"SIG_INVALID": ("COUNTERFEIT_SUSPECT", "HIGH", True), "TOKEN_MALFORMED": ("COUNTERFEIT_SUSPECT", "HIGH", True),
                 "ALREADY_LOADED": ("DUPLICATE_LOAD", "MEDIUM", False), "QUARANTINED": ("QUARANTINE_BLOCK", "HIGH", True),
                 "PASSPORT_REVOKED": ("REVOKED_SCAN", "HIGH", True), "TOKEN_MISMATCH": ("REVOKED_SCAN", "MEDIUM", False)}
    for c in codes:
        if c in alert_map:
            t, sev, loud = alert_map[c]
            raise_alert(db, type=t, severity=sev, entity_type="SHIPMENT", entity_id=s.id, reason_codes=[c],
                        details={"scan_id": sc.id, "package": pkg.label if pkg else None, "shipment": s.code}, dedupe_key=f"scan:{sc.id}:{t}", audible=loud)
    return scan_view(db, sc), True


# ── close, depart, deliver ─────────────────────────────────────────────────
def _lock_shipment_contents(db: Session, s0: m.Shipment) -> tuple[LockSet, list[m.Package], m.Shipment]:
    pkg_ids = list(db.scalars(select(m.Load.package_id).where(m.Load.shipment_id == s0.id, m.Load.removed_at.is_(None))))
    unit_ids = list(db.scalars(select(m.PackageItem.unit_id).where(m.PackageItem.package_id.in_(pkg_ids), m.PackageItem.removed_at.is_(None))))
    locks = lock_set(db)
    locks.lock(m.Unit, unit_ids)
    pkgs = locks.lock(m.Package, pkg_ids)
    locks.lock(m.Order, [s0.order_id])
    [s] = locks.lock(m.Shipment, [s0.id])
    # re-read membership under lock; a concurrent change means the caller must retry
    if set(db.scalars(select(m.Load.package_id).where(m.Load.shipment_id == s.id, m.Load.removed_at.is_(None)))) != set(pkg_ids):
        raise ApiError(409, "MANIFEST_CHANGED", "Loads changed concurrently — retry")
    return locks, pkgs, s


def _revalidate(db: Session, pkgs: list[m.Package], s: m.Shipment) -> list[dict]:
    """Every loaded package must still be authentic, current, unheld and intact."""
    problems = []
    for pkg in pkgs:
        units = [db.get(m.Unit, i.unit_id) for i in open_items(db, pkg.id)]
        life, cont, quar = package_blockers(db, pkg, units)
        chk = last_check(db, pkg)
        seal = [] if chk and chk.kind == "RECHECK" and chk.result == "INTACT" and pkg.state == "LOADED" else [{"code": "SEAL_NOT_INTACT", "message": f"{pkg.label} seal state changed"}]
        issues = life + cont + quar + seal
        if issues:
            problems.append({"package_id": pkg.id, "label": pkg.label, "blockers": issues})
    return problems


def _manifest_blockers(db: Session, s: m.Shipment, pkgs: list[m.Package]) -> list[dict]:
    """The manifest must still describe a real load: something on the truck, and the agreed quantity.
    Checked at close AND at departure, because crates can come off a closed shipment (removal tasks)."""
    out = []
    loaded, exp = loaded_units(db, s.id), expected_units(db, s)
    if not pkgs or loaded == 0:
        out.append({"code": "EMPTY_MANIFEST", "message": "Nothing loaded"})
    elif loaded != exp and not policies.settings_of(policies.active(db)).allow_partial_close:
        out.append({"code": "QTY_MISMATCH", "message": f"{loaded} loaded ≠ {exp} expected"})
    return out


def _seal_age_blockers(db: Session, pkgs: list[m.Package]) -> list[dict]:
    """An INTACT recheck is evidence for a limited time only. A crate whose last recheck has aged out is not unloaded:
    it is rechecked on the truck. (A recheck that is not INTACT is handled by _revalidate, which requires removal.)"""
    max_age = policies.settings_of(policies.active(db)).seal_recheck_max_age_minutes
    now, out = datetime.now(UTC), []
    for pkg in pkgs:
        chk = last_check(db, pkg)
        if chk and chk.kind == "RECHECK" and chk.result == "INTACT" and now - chk.created_at > timedelta(minutes=max_age):
            out.append({"code": "SEAL_RECHECK_EXPIRED", "package_id": pkg.id,
                        "message": f"{pkg.label}: last INTACT recheck is older than {max_age} min — recheck the seal on the truck"})
    return out


def close(db: Session, p: Principal, shipment_key: str, unsynced: int = 0) -> dict:
    s0 = find_shipment(db, shipment_key)
    locks, pkgs, s = _lock_shipment_contents(db, s0)
    if s.status != "OPEN":
        raise ApiError(409, "MANIFEST_CLOSED", f"Shipment is {s.status}")
    blockers = _manifest_blockers(db, s, pkgs)
    loaded = loaded_units(db, s.id)
    if unsynced:
        blockers.append({"code": "PROVISIONAL_PENDING", "message": f"{unsynced} offline scan(s) on this device are not synced yet"})
    blockers += _reconciliation_blockers(db, s)
    blockers += _seal_age_blockers(db, pkgs)
    for prob in _revalidate(db, pkgs, s):
        blockers.append({"code": "LOADED_PACKAGE_BLOCKED", "message": f"{prob['label']}: {', '.join(b['code'] for b in prob['blockers'])} — unload required", "package_id": prob["package_id"]})
    if blockers:
        raise ApiError(409, "MANIFEST_NOT_RECONCILED", "Manifest cannot close", {"blockers": blockers})
    s.status, s.closed_at = "CLOSED", datetime.now(UTC)
    db.flush()
    audit.append(db, p.username, "MANIFEST_CLOSED", "shipment", s.id, data={"units": loaded, "packages": len(pkgs)})
    outbox.emit(db, "DISPATCH_STATE_CHANGED", "SHIPMENT", s.id, payload={"status": "CLOSED", "units": loaded})
    return shipment_view(db, s)


def _occurred(occurred_at: datetime | None, not_before: datetime | None) -> datetime:
    now = datetime.now(UTC)
    t = occurred_at.astimezone(UTC) if occurred_at else now
    if t > now + timedelta(minutes=5):
        raise ApiError(422, "OCCURRED_AT_IN_FUTURE", "Movement time is in the future")
    if not_before and t < not_before:
        raise ApiError(422, "OCCURRED_AT_OUT_OF_ORDER", "Movement precedes the previous state change")
    return t


def movement(db: Session, p: Principal, shipment_key: str, type: str, location: str, occurred_at: datetime | None) -> tuple[dict, int]:
    """Returns (body, http_status). A blocked departure returns 409 but COMMITS the removal tasks it created."""
    s0 = find_shipment(db, shipment_key)
    locks, pkgs, s = _lock_shipment_contents(db, s0)
    last = db.scalars(select(m.MovementEvent).where(m.MovementEvent.shipment_id == s.id).order_by(m.MovementEvent.occurred_at.desc()).limit(1)).first()
    if type == "DEPARTED":
        if s.status != "CLOSED":
            raise ApiError(409, "MANIFEST_OPEN" if s.status == "OPEN" else "ALREADY_DEPARTED", f"Shipment is {s.status}; close the manifest first")
        t = _occurred(occurred_at, s.closed_at)
        open_items_ = _manifest_blockers(db, s, pkgs) + _reconciliation_blockers(db, s) + _seal_age_blockers(db, pkgs)
        if open_items_:   # e.g. a late offline sync refused a crate after close: it must be dealt with before the truck leaves
            raise ApiError(409, "DEPARTURE_NOT_RECONCILED", "Shipment cannot depart", {"blockers": open_items_})
        problems = _revalidate(db, pkgs, s)
        if problems:
            tasks = [segregation.create(db, type="REMOVAL_REQUIRED", dedupe_key=f"removal:{pr['package_id']}:{s.id}", package_id=pr["package_id"], shipment_id=s.id,
                                        reason="; ".join(b["code"] for b in pr["blockers"])) for pr in problems]
            s.status = "OPEN"   # reopen so the package can be unloaded and the manifest re-closed
            s.closed_at = None
            db.flush()
            audit.append(db, p.username, "DEPARTURE_BLOCKED", "shipment", s.id, data={"packages": [pr["label"] for pr in problems]})
            raise_alert(db, type="DEPARTURE_BLOCKED", severity="CRITICAL", entity_type="SHIPMENT", entity_id=s.id, reason_codes=sorted({b["code"] for pr in problems for b in pr["blockers"]}),
                        details={"shipment": s.code, "packages": [pr["label"] for pr in problems]}, dedupe_key=f"depart:{s.id}:{','.join(sorted(pr['package_id'] for pr in problems))}", audible=True)
            return {"error": {"code": "DEPARTURE_BLOCKED", "message": "Loaded packages no longer pass the gate; removal required",
                              "details": {"problems": problems, "segregation_task_ids": [t for t in tasks if t]}}}, 409
        manifest = {"shipment": s.id, "code": s.code, "order": s.order_id, "vehicle": s.vehicle_no, "departed_at": _iso(t),
                    "packages": sorted([{"id": k.id, "label": k.label, "token_sha256": hashlib.sha256((k.token or "").encode()).hexdigest(),
                                         "units": sorted(db.get(m.Unit, i.unit_id).serial for i in open_items(db, k.id))} for k in pkgs], key=lambda x: x["id"])}
        s.manifest_sha256 = _sha(manifest)
        s.status = "IN_TRANSIT"
        for k in pkgs:
            k.state = "IN_TRANSIT"
    elif type == "CHECKPOINT":
        if s.status != "IN_TRANSIT":
            raise ApiError(409, "NOT_IN_TRANSIT", "Shipment has not departed")
        t = _occurred(occurred_at, last.occurred_at if last else None)
    else:  # DELIVERED
        if s.status != "IN_TRANSIT":
            raise ApiError(409, "NOT_IN_TRANSIT", "Delivery requires a recorded departure")
        t = _occurred(occurred_at, last.occurred_at if last else None)
        s.status = "DELIVERED"
        for k in pkgs:
            k.state = "DELIVERED"
    db.add(m.MovementEvent(id=new_id("mv"), shipment_id=s.id, type=type, location=location.strip() or "—", occurred_at=t, actor_id=p.id))
    db.flush()
    audit.append(db, p.username, f"SHIPMENT_{type}", "shipment", s.id, data={"location": location, "occurred_at": _iso(t), "manifest_sha256": s.manifest_sha256})
    outbox.emit(db, "DISPATCH_STATE_CHANGED", "SHIPMENT", s.id, payload={"status": s.status, "movement": type, "location": location})
    return shipment_view(db, s), 200
