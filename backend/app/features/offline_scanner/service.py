"""Offline dock scanning: signed v2 snapshots and idempotent, chain-verified replay (CONTRACT §8.7, §10.3). Owner: Opus.

- A snapshot is scoped to one shipment, one registered device and its bound operator, the active policy version and
  the order's REMAINING capacity after all committed loads. It is signed with Astra's AQ1 v2 builder and expires.
- Offline decisions are provisional. On sync the server verifies the device hash chain from its stored head, requires
  the contiguous next sequence, then replays every event through the SAME dispatch gate:
    PROVISIONAL_ACCEPT → ACCEPTED (loaded) or CONFLICT (+ durable SEGREGATION_REQUIRED task + alert)
    LOCAL_BLOCK / ONLINE without a server record → BLOCKED (recorded, never loaded)
    already-synced event, same content → DUPLICATE (original outcome); changed content → whole batch refused
- Each event commits atomically with the device checkpoint (scan + device_event + head). A failure mid-batch leaves a
  consistent prefix; the client resends from the next sequence.
The chain detects edits, gaps and reordering after capture. It does not authenticate the phone or prove that nothing
was deleted before the first sync.
"""
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core import audit
from app.core.auth import Principal
from app.core.errors import ApiError
from app.core.ids import new_id
from app.core.signing import load_signing_key, register_active_key
from app.db import models as m
from app.db.session import session_factory
from app.features.alerts.service import raise_alert
from app.features.dispatch import segregation
from app.features.dispatch import service as dispatch
from app.features.inspection_passports.passports import trusted_keys
from app.features.packaging.service import last_check, open_items
from app.features.passport_crypto.service import CryptoError, build_snapshot, chain_hash
from app.policy import eligibility, policies

GENESIS = "0" * 64
LOCAL = {"PROVISIONAL_ACCEPT", "LOCAL_BLOCK", "ONLINE"}


def _iso(dt):
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z") if dt else None


def _device(db: Session, p: Principal, device_id: str, *, create: bool) -> m.Device:
    d = db.scalars(select(m.Device).where(m.Device.id == device_id).with_for_update()).first()
    if not d:
        if not create:
            raise ApiError(404, "DEVICE_UNKNOWN", "Device has never received a snapshot; download one while online")
        d = m.Device(id=device_id, operator_id=p.id, last_seq=0, head_hash=GENESIS)
        db.add(d); db.flush()
        audit.append(db, p.username, "DEVICE_REGISTERED", "device", device_id)
    if d.operator_id != p.id:
        raise ApiError(409, "DEVICE_OPERATOR_MISMATCH", "This device is bound to another operator", {"device_id": device_id})
    return d


def _package_blocked(db: Session, pkg: m.Package) -> bool:
    units = [db.get(m.Unit, i.unit_id) for i in open_items(db, pkg.id)]
    if pkg.state == "SEAL_REVIEW" or any(not eligibility.compute(db, u).dispatch_eligible for u in units):
        return True
    chk = last_check(db, pkg)
    return bool(chk and chk.result in ("SUSPICIOUS", "UNVERIFIABLE"))


def snapshot(db: Session, p: Principal, shipment_key: str, device_id: str) -> dict:
    s = dispatch.find_shipment(db, shipment_key)
    if s.status != "OPEN":
        raise ApiError(409, "MANIFEST_CLOSED", f"Shipment {s.code} is {s.status}")
    dev = _device(db, p, device_id, create=True)
    order = db.get(m.Order, s.order_id)
    part = db.get(m.Part, order.part_id)
    pol = policies.active(db)
    ttl = policies.settings_of(pol).snapshot_ttl_minutes * 60
    remaining = order.qty_units - dispatch.order_committed_units(db, order.id)
    pkgs = list(db.scalars(select(m.Package).where(m.Package.order_id == order.id, m.Package.state.in_(("SEALED", "SEAL_REVIEW"))).order_by(m.Package.id)))
    allowed = [k.id for k in pkgs if not dispatch.active_load(db, k.id)]
    held = sorted(k.id for k in pkgs if k.id in allowed and _package_blocked(db, k))
    kid, key = load_signing_key()
    register_active_key(db)
    keys = trusted_keys(db)
    now = datetime.now(UTC)
    sid = new_id("snap")
    try:
        token = build_snapshot(snapshot_id=sid, iat=int(now.timestamp()), ttl_seconds=ttl, shipment=s.id, order=order.id, part=part.part_number,
                               device_id=dev.id, operator_id=p.id, policy_version=pol.version, expected_units=max(0, remaining),
                               allowed_packages=allowed, held=held, revoked_kids=[k.kid for k in keys if k.status == "REVOKED"],
                               keys=[k for k in keys if k.status != "REVOKED"], kid=kid, key=key)
    except CryptoError as e:
        raise ApiError(500, e.code, f"Snapshot signing failed: {e.message}", {"component": "passport_crypto"})
    db.add(m.OfflineSnapshot(id=sid, device_id=dev.id, shipment_id=s.id, operator_id=p.id, kid=kid, token=token, issued_at=now, expires_at=now + timedelta(seconds=ttl)))
    db.flush()
    audit.append(db, p.username, "OFFLINE_SNAPSHOT_ISSUED", "shipment", s.id, data={"snapshot": sid, "device": dev.id, "allowed": len(allowed), "held": len(held),
                                                                                    "expected_units": remaining, "ttl_s": ttl})
    return {"token": token, "snapshot_id": sid, "expires_at": _iso(now + timedelta(seconds=ttl)), "device": {"id": dev.id, "last_seq": dev.last_seq, "head": dev.head_hash}}


def _verify_batch(head: str, last_seq: int, events: list[dict]) -> tuple[list[dict], list[dict], int | None]:
    """(already-synced, new, broken_at). New events must continue the chain contiguously from the stored head."""
    old = [e for e in events if e["seq"] <= last_seq]
    new = sorted((e for e in events if e["seq"] > last_seq), key=lambda e: e["seq"])
    prev, expect = head, last_seq + 1
    for e in new:
        try:
            ok = e["seq"] == expect and e["prev_hash"] == prev and chain_hash(prev, e) == e["hash"]
        except CryptoError:
            ok = False
        if not ok:
            return old, new, e["seq"]
        prev, expect = e["hash"], expect + 1
    return old, new, None


def sync(db: Session, p: Principal, device_id: str, snapshot_id: str | None, events: list[dict]) -> dict:
    dev = _device(db, p, device_id, create=False)
    for e in events:
        if e.get("local_decision") not in LOCAL:
            raise ApiError(422, "VALIDATION", f"local_decision must be one of {sorted(LOCAL)}")
    snap = db.get(m.OfflineSnapshot, snapshot_id) if snapshot_id else None
    if snapshot_id and (not snap or snap.device_id != dev.id):
        raise ApiError(409, "SNAPSHOT_SCOPE_MISMATCH", "Snapshot was not issued to this device")
    old, new, broken = _verify_batch(dev.head_hash, dev.last_seq, events)
    results = []
    for e in old:   # idempotent replay: compare content with what was accepted before
        prior = db.get(m.DeviceEvent, (dev.id, e["seq"]))
        try:  # recompute from CONTENT: a changed event that reuses the old hash must not pass as a duplicate
            same = bool(prior) and prior.client_event_id == e["client_event_id"] and chain_hash(prior.prev_hash, e) == prior.hash == e["hash"]
        except CryptoError:
            same = False
        if not same:
            raise ApiError(409, "EVENT_CONTENT_CHANGED", f"Event seq {e['seq']} differs from the version already synced", {"seq": e["seq"]})
        sc = db.get(m.Scan, prior.scan_id) if prior.scan_id else None
        results.append({"client_event_id": e["client_event_id"], "seq": e["seq"], "outcome": "DUPLICATE", "original_outcome": prior.outcome,
                        "scan": dispatch.scan_view(db, sc, replay=True) if sc else None, "note": "Already synced"})
    if broken is not None:
        audit.append(db, p.username, "DEVICE_CHAIN_BROKEN", "device", dev.id, f"Chain broken at seq {broken}", data={"head_seq": dev.last_seq})
        raise_alert(db, type="OFFLINE_CHAIN_BROKEN", severity="HIGH", entity_type="DEVICE", entity_id=dev.id, reason_codes=["CHAIN_BROKEN"],
                    details={"broken_at": broken, "server_seq": dev.last_seq}, dedupe_key=f"chain:{dev.id}:{dev.last_seq}:{broken}")
        return {"results": [], "chain": {"ok": False, "broken_at": broken, "server_head": dev.head_hash, "server_seq": dev.last_seq}}
    db.commit()  # release the device row; each new event commits in its own transaction below
    for e in new:
        results.append(_apply(p, dev.id, snapshot_id, e))
    with session_factory()() as s2:
        d = s2.get(m.Device, dev.id)
        return {"results": results, "chain": {"ok": True, "server_head": d.head_hash, "server_seq": d.last_seq}}


def _apply(p: Principal, device_id: str, snapshot_id: str | None, e: dict) -> dict:
    """One event = one transaction: gate replay + device_event + checkpoint advance."""
    with session_factory()() as db:
        dev = db.scalars(select(m.Device).where(m.Device.id == device_id).with_for_update()).one()
        if dev.last_seq != e["seq"] - 1 or dev.head_hash != e["prev_hash"]:
            raise ApiError(409, "SYNC_RACE", "Another sync advanced this device concurrently; resend from the server head")
        scanned_at = datetime.fromisoformat(e["scanned_at"].replace("Z", "+00:00"))
        snap = db.get(m.OfflineSnapshot, snapshot_id) if snapshot_id else None
        ld = e["local_decision"]
        note, outcome, task = "", None, None
        prior = db.scalars(select(m.Scan).where(m.Scan.client_event_id == e["client_event_id"])).first()
        if ld == "ONLINE":
            if prior:
                view, outcome, note = dispatch.scan_view(db, prior, replay=True), "DUPLICATE", "Online decision already recorded"
            else:
                view, _ = dispatch.scan(db, p, e["shipment_id"], e["token"], e["client_event_id"], device_id, offline=True, captured_at=scanned_at, commit_load=False)
                outcome, note = "BLOCKED", "Online request never reached the server — recorded, not loaded"
        elif ld == "LOCAL_BLOCK":
            view, _ = dispatch.scan(db, p, e["shipment_id"], e["token"], e["client_event_id"], device_id, offline=True, captured_at=scanned_at, commit_load=False)
            outcome, note = "BLOCKED", "Blocked on device — recorded, not loaded"
        else:
            stale = snap is not None and scanned_at >= snap.expires_at
            view, _ = dispatch.scan(db, p, e["shipment_id"], e["token"], e["client_event_id"], device_id, offline=True, captured_at=scanned_at, commit_load=not stale)
            if view["decision"] == "ACCEPTED":
                outcome = "ACCEPTED"
            else:
                outcome = "CONFLICT"
                codes = ["SNAPSHOT_STALE"] if stale else [r["code"] for r in view["reasons"]]
                label = view.get("package_label") or "unidentified package"
                note = f"Server refused the provisional load ({', '.join(codes)}): physically unload and segregate {label}"
                task = segregation.create(db, type="SEGREGATION_REQUIRED", dedupe_key=f"offline:{e['client_event_id']}", scan_id=view["scan_id"],
                                          package_id=view.get("package_id"), shipment_id=view["shipment_id"], reason=note)
                raise_alert(db, type="OFFLINE_CONFLICT", severity="HIGH", entity_type="SHIPMENT", entity_id=view["shipment_id"], reason_codes=codes,
                            details={"client_event_id": e["client_event_id"], "device_id": device_id, "package": label, "task_id": task},
                            dedupe_key=f"offline-conflict:{e['client_event_id']}", audible=True)
        db.add(m.DeviceEvent(device_id=device_id, seq=e["seq"], client_event_id=e["client_event_id"], prev_hash=e["prev_hash"], hash=e["hash"],
                             snapshot_id=snapshot_id, scan_id=view["scan_id"] if view else None, local_decision=ld, outcome=outcome, scanned_at=scanned_at,
                             operator_id=p.id))
        dev.last_seq, dev.head_hash = e["seq"], e["hash"]
        db.commit()
        return {"client_event_id": e["client_event_id"], "seq": e["seq"], "outcome": outcome, "scan": view, "note": note, "segregation_task_id": task}


def pending_for_shipment(db: Session, shipment_id: str) -> list[dict]:
    """Devices holding a still-valid snapshot for this shipment that have not synced since it was issued (best effort)."""
    out = []
    now = datetime.now(UTC)
    for sn in db.scalars(select(m.OfflineSnapshot).where(m.OfflineSnapshot.shipment_id == shipment_id, m.OfflineSnapshot.expires_at > now)):
        last = db.scalar(select(m.DeviceEvent.synced_at).where(m.DeviceEvent.device_id == sn.device_id).order_by(m.DeviceEvent.synced_at.desc()).limit(1))
        if last is None or last < sn.issued_at:
            out.append({"device_id": sn.device_id, "snapshot_id": sn.id, "issued_at": _iso(sn.issued_at)})
    return out
