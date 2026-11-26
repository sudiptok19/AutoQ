"""Offline dock scanning: signed v2 snapshots and idempotent, chain-verified replay (CONTRACT §8.7, §10.3). Owner: Opus.

Authorization
- A snapshot authorizes ONE device, bound to ONE operator, to capture offline for ONE shipment, against the order's
  remaining capacity, the signed allowlist and the held list at issue time. It is signed with Astra's AQ1 v2 builder.
- A snapshot stays OUTSTANDING until it is settled, and an outstanding snapshot blocks manifest close and departure:
    ACKNOWLEDGED  the device ended its offline session with an empty queue at the server's chain head
    SUPERSEDED    the device refreshed with an empty queue: its OLDER snapshots for that shipment are settled, never
                  the one just issued (that one authorizes further captures)
    RECOVERED     a lost device: a Quality Manager REQUESTS recovery (offline authority ends at once, a
                  DEVICE_RECONCILIATION task is raised), and the snapshot settles only with linked physical
                  reconciliation evidence or when that task is completed
  "Empty queue at the head" binds the terminal sequence AND hash. Expiry settles nothing.
- Load authority is judged at RECEIPT: a provisional event gains automatic load authority only if the server receives
  it while its snapshot is unsettled, not under recovery and not yet expired. Anything received later needs physical
  reconciliation (CONFLICT + task), whatever capture time the device claims.
- Issuance, settlement, recovery and close are serialized on the shipment row. Lock order everywhere in this module:
  device row FIRST, then the business hierarchy (heat → … → shipment) through `lock_set`.

Replay (sync)
- The server verifies the device hash chain from its stored head, requires the contiguous next sequence, then replays
  each event through the SAME dispatch gate, one transaction per event (scan + device_event + checkpoint):
    PROVISIONAL_ACCEPT → ACCEPTED, only if its snapshot (cited per event) belongs to this device, operator and shipment,
                         was valid and unsettled at capture, and its SIGNED allowlist/held list permitted the package;
                         otherwise CONFLICT (+ durable segregation task + alert)
    LOCAL_BLOCK        → BLOCKED (recorded, never loaded)
    ONLINE             → DUPLICATE of the recorded online scan if (and only if) that scan has the same shipment+token
                         fingerprint, device and operator; BLOCKED if the request never reached the server
  An existing scan with the event's id that this capture did not produce is never adopted: CONFLICT, nothing linked.
- Already-synced events: same content and same snapshot claim → DUPLICATE with the ORIGINAL outcome (a CONFLICT repeats
  its physical instruction and task, so a lost response never loses an unload order); anything else → batch refused.
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
from app.db.locking import lock_set
from app.db.session import session_factory
from app.features.alerts.service import raise_alert
from app.features.dispatch import segregation
from app.features.dispatch import service as dispatch
from app.features.inspection_passports.passports import trusted_keys
from app.features.packaging.service import last_check, open_items
from app.features.passport_crypto.service import CryptoError, build_snapshot, chain_hash, verify
from app.policy import eligibility, policies

GENESIS = "0" * 64
LOCAL = {"PROVISIONAL_ACCEPT", "LOCAL_BLOCK", "ONLINE"}
CLOCK_SKEW = timedelta(minutes=2)   # phone clocks drift; tolerated at the window edges, never more
SUPPORTED_CHAIN_VERSIONS = (1, 2)   # dual reader: every supported version verifies, whatever writers are enabled


def _now() -> datetime:
    """Authoritative server receipt time (one seam, so tests can move the clock instead of editing signed records)."""
    return datetime.now(UTC)


def _chain_policy(db: Session) -> dict:
    o = policies.settings_of(policies.active(db)).offline
    return {"writer_version": o.chain_writer_version, "min_version": o.chain_min_version, "supported": list(SUPPORTED_CHAIN_VERSIONS)}


def _iso(dt):
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z") if dt else None


def parse_capture_time(value: str) -> datetime:
    """Capture times must carry a timezone (`Z` or ±hh:mm): a naive time cannot be placed in a snapshot's window."""
    try:
        t = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ApiError(422, "VALIDATION", f"scanned_at {value!r} is not an ISO-8601 time")
    if t.tzinfo is None or t.utcoffset() is None:
        raise ApiError(422, "VALIDATION", f"scanned_at {value!r} has no timezone; send UTC with a trailing Z")
    return t.astimezone(UTC)


def _device(db: Session, p: Principal, device_id: str, *, create: bool) -> m.Device:
    d = db.scalars(select(m.Device).where(m.Device.id == device_id).with_for_update().execution_options(populate_existing=True)).first()
    if not d:
        if not create:
            raise ApiError(404, "DEVICE_UNKNOWN", "Device has never received a snapshot; download one while online")
        d = m.Device(id=device_id, operator_id=p.id, last_seq=0, head_hash=GENESIS)
        db.add(d); db.flush()
        d.just_registered = True   # the caller audits this after taking its business locks (audit rows come last)
    if d.operator_id != p.id:
        raise ApiError(409, "DEVICE_OPERATOR_MISMATCH", "This device is bound to another operator", {"device_id": device_id})
    return d


def _package_blocked(db: Session, pkg: m.Package) -> bool:
    units = [db.get(m.Unit, i.unit_id) for i in open_items(db, pkg.id)]
    if pkg.state == "SEAL_REVIEW" or any(not eligibility.compute(db, u).dispatch_eligible for u in units):
        return True
    chk = last_check(db, pkg)
    return bool(chk and chk.result in ("SUSPICIOUS", "UNVERIFIABLE"))


def snapshot_view(db: Session, sn: m.OfflineSnapshot) -> dict:
    dev = db.get(m.Device, sn.device_id)
    return {"snapshot_id": sn.id, "device_id": sn.device_id, "operator": db.get(m.User, sn.operator_id).username, "shipment_id": sn.shipment_id,
            "issued_at": _iso(sn.issued_at), "expires_at": _iso(sn.expires_at), "expired": sn.expires_at <= _now(),
            "device_seq": dev.last_seq if dev else None, "settlement": sn.settlement, "settled_at": _iso(sn.settled_at),
            "settled_by": db.get(m.User, sn.settled_by).username if sn.settled_by else None, "settlement_reason": sn.settlement_reason,
            "settled_seq": sn.settled_seq, "settled_head": sn.settled_head, "recovery_requested_at": _iso(sn.recovery_requested_at),
            "recovery_task_id": sn.recovery_task_id, "recovery_evidence": sn.recovery_evidence or []}


def outstanding_for_shipment(db: Session, shipment_id: str) -> list[dict]:
    """Unsettled offline authorizations for a shipment, expired or not. Manifest close and departure refuse while any exist."""
    rows = db.scalars(select(m.OfflineSnapshot).where(m.OfflineSnapshot.shipment_id == shipment_id, m.OfflineSnapshot.settled_at.is_(None))
                      .order_by(m.OfflineSnapshot.issued_at))
    return [snapshot_view(db, sn) for sn in rows]


def _settle(db: Session, sn: m.OfflineSnapshot, how: str, actor_id: str, dev: m.Device, reason: str | None, now: datetime) -> None:
    sn.settled_at, sn.settlement, sn.settled_by, sn.settlement_reason = now, how, actor_id, reason
    sn.settled_seq, sn.settled_head = dev.last_seq, dev.head_hash


def _queue_empty_at_head(dev: m.Device, local_seq: int | None, local_head: str | None, pending: int | None) -> bool:
    """The device attests it holds nothing the server lacks: no pending events, and its chain ends exactly where the
    server's does (sequence AND hash — a same-length chain with different content is not the same history)."""
    return pending == 0 and local_seq is not None and local_seq == dev.last_seq and local_head == dev.head_hash


# ── issuance, settlement, recovery ─────────────────────────────────────────
def snapshot(db: Session, p: Principal, shipment_key: str, device_id: str, *, local_seq: int | None = None, local_head: str | None = None,
             pending: int | None = None) -> dict:
    dev = _device(db, p, device_id, create=True)                       # device row first …
    s0 = dispatch.find_shipment(db, shipment_key)
    [s] = lock_set(db).lock(m.Shipment, [s0.id])                       # … then the shipment: serializes with close/settlement
    if s.status != "OPEN":
        raise ApiError(409, "MANIFEST_CLOSED", f"Shipment {s.code} is {s.status}")
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
    # A refresh from a device with nothing unsynced settles its OLDER authorizations for this shipment. The new one is
    # an authorization to capture more, so it stays outstanding until acknowledged or recovered.
    superseded = []
    if _queue_empty_at_head(dev, local_seq, local_head, pending):
        for old in db.scalars(select(m.OfflineSnapshot).where(m.OfflineSnapshot.device_id == dev.id, m.OfflineSnapshot.shipment_id == s.id,
                                                              m.OfflineSnapshot.settled_at.is_(None)).with_for_update().execution_options(populate_existing=True)):
            _settle(db, old, "SUPERSEDED", p.id, dev, f"Refreshed by {sid} with an empty queue", now)
            superseded.append(old.id)
    db.add(m.OfflineSnapshot(id=sid, device_id=dev.id, shipment_id=s.id, operator_id=p.id, kid=kid, token=token, issued_at=now, expires_at=now + timedelta(seconds=ttl)))
    db.flush()
    if getattr(dev, "just_registered", False):
        audit.append(db, p.username, "DEVICE_REGISTERED", "device", dev.id)
    audit.append(db, p.username, "OFFLINE_SNAPSHOT_ISSUED", "shipment", s.id, data={"snapshot": sid, "device": dev.id, "allowed": len(allowed), "held": len(held),
                                                                                    "expected_units": remaining, "ttl_s": ttl, "superseded": superseded})
    return {"token": token, "snapshot_id": sid, "expires_at": _iso(now + timedelta(seconds=ttl)), "superseded": superseded,
            "device": {"id": dev.id, "last_seq": dev.last_seq, "head": dev.head_hash, "chain_version_high": dev.chain_version_high},
            "chain": _chain_policy(db)}


def settle(db: Session, p: Principal, snapshot_id: str, device_id: str, local_seq: int, local_head: str, pending: int) -> dict:
    """The device hands back its offline authorization. Only possible with nothing unsynced: the server must already hold
    every event captured under it. After settlement the snapshot authorizes nothing (later captures → CONFLICT)."""
    dev = _device(db, p, device_id, create=False)
    sn = db.get(m.OfflineSnapshot, snapshot_id)
    if not sn or sn.device_id != dev.id or sn.operator_id != p.id:
        raise ApiError(404, "SNAPSHOT_NOT_FOUND", "No such snapshot for this device and operator")
    lock_set(db).lock(m.Shipment, [sn.shipment_id])
    db.refresh(sn, with_for_update=True)
    if sn.settlement == "RECOVERED" or sn.recovery_requested_at:
        raise ApiError(409, "SNAPSHOT_RECOVERED", "This device was declared lost; its captures need physical verification", snapshot_view(db, sn))
    if sn.settled_at:
        return snapshot_view(db, sn)                                     # idempotent
    if not _queue_empty_at_head(dev, local_seq, local_head, pending):
        raise ApiError(409, "SETTLEMENT_PENDING_EVENTS", "Sync every queued event before ending the offline session",
                       {"server_seq": dev.last_seq, "server_head": dev.head_hash, "local_seq": local_seq, "local_head": local_head, "pending": pending})
    _settle(db, sn, "ACKNOWLEDGED", p.id, dev, None, datetime.now(UTC))
    db.flush()
    audit.append(db, p.username, "OFFLINE_SNAPSHOT_SETTLED", "shipment", sn.shipment_id, data={"snapshot": sn.id, "device": dev.id, "seq": dev.last_seq, "head": dev.head_hash})
    return snapshot_view(db, sn)


def recover(db: Session, p: Principal, snapshot_id: str, reason: str, evidence_ids: list[str] | None = None) -> dict:
    """Lost or unavailable device. Recovery ends the snapshot's offline authority at once, but does not by itself clear
    the shipment: nothing is assumed reconciled. It settles only with linked physical reconciliation evidence (an
    uploaded count/inspection record) or when the DEVICE_RECONCILIATION task it raises is completed."""
    sn0 = db.get(m.OfflineSnapshot, snapshot_id)
    if not sn0:
        raise ApiError(404, "SNAPSHOT_NOT_FOUND", "Snapshot not found")
    dev = db.scalars(select(m.Device).where(m.Device.id == sn0.device_id).with_for_update().execution_options(populate_existing=True)).one()
    [ship] = lock_set(db).lock(m.Shipment, [sn0.shipment_id])
    sn = db.scalars(select(m.OfflineSnapshot).where(m.OfflineSnapshot.id == snapshot_id).with_for_update().execution_options(populate_existing=True)).one()
    if sn.settled_at:
        raise ApiError(409, "SNAPSHOT_SETTLED", f"Already settled ({sn.settlement})", snapshot_view(db, sn))
    evidence = []
    for sha in dict.fromkeys(evidence_ids or []):
        ev = db.get(m.Evidence, sha)
        if not ev:
            raise ApiError(422, "EVIDENCE_NOT_FOUND", f"Evidence {sha[:12]}… does not exist; upload the reconciliation record first")
        evidence.append({"sha256": ev.sha256, "media_type": ev.media_type, "evidence_source": ev.evidence_source})
    now = datetime.now(UTC)
    first = sn.recovery_requested_at is None
    if first:
        sn.recovery_requested_at = now
        sn.recovery_task_id = segregation.create(
            db, type="DEVICE_RECONCILIATION", dedupe_key=f"device-recon:{sn.id}", shipment_id=ship.id,
            reason=f"Device {dev.id} ({db.get(m.User, dev.operator_id).username}) is unavailable: physically reconcile {ship.code}'s truck and "
                   f"staging lane against the manifest before release. Recovery reason: {reason}",
            consignee_action={"action": "Count the crates on the truck and in staging; compare with the manifest; record what was found"})
    elif not evidence:
        raise ApiError(409, "RECOVERY_IN_PROGRESS", "Recovery already requested: complete its reconciliation task or attach reconciliation evidence",
                       snapshot_view(db, sn))
    settled = bool(evidence)
    if settled:
        sn.recovery_evidence = evidence
        _settle(db, sn, "RECOVERED", p.id, dev, reason, now)
        task = db.scalars(select(m.SegregationTask).where(m.SegregationTask.id == sn.recovery_task_id).with_for_update()
                          .execution_options(populate_existing=True)).first()
        if task and task.status == "OPEN":
            task.status, task.completed_by, task.completed_at = "COMPLETED", p.id, now
            task.completion_reason = f"Closed by linked reconciliation evidence: {', '.join(e['sha256'][:12] for e in evidence)}"
    db.flush()
    audit.append(db, p.username, "OFFLINE_DEVICE_RECOVERED" if settled else "OFFLINE_DEVICE_RECOVERY_REQUESTED", "device", dev.id, reason,
                 data={"snapshot": sn.id, "shipment": sn.shipment_id, "server_seq": dev.last_seq, "server_head": dev.head_hash,
                       "evidence": [e["sha256"] for e in evidence], "task": sn.recovery_task_id})
    if first:
        raise_alert(db, type="OFFLINE_DEVICE_RECOVERED", severity="HIGH", entity_type="SHIPMENT", entity_id=sn.shipment_id, reason_codes=["DEVICE_RECOVERED"],
                    details={"device_id": dev.id, "snapshot_id": sn.id, "operator": db.get(m.User, dev.operator_id).username, "reason": reason},
                    dedupe_key=f"recover:{sn.id}")
    return snapshot_view(db, sn)


def complete_recovery(db: Session, p: Principal, task: m.SegregationTask, now: datetime) -> None:
    """Called inside segregation.complete (shipment already locked) when a DEVICE_RECONCILIATION task is completed:
    the physical reconciliation is done, so the lost device's snapshot settles."""
    sn = db.scalars(select(m.OfflineSnapshot).where(m.OfflineSnapshot.recovery_task_id == task.id).with_for_update()
                    .execution_options(populate_existing=True)).first()
    if sn and not sn.settled_at:
        dev = db.get(m.Device, sn.device_id)
        _settle(db, sn, "RECOVERED", p.id, dev, f"Reconciled physically: {task.completion_reason}", now)
        sn.recovery_evidence = [*(sn.recovery_evidence or []), {"task_id": task.id}]


# ── sync ───────────────────────────────────────────────────────────────────
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


def _replay_result(db: Session, prior: m.DeviceEvent, e: dict) -> dict:
    sc = db.get(m.Scan, prior.scan_id) if prior.scan_id else None
    res = {"client_event_id": e["client_event_id"], "seq": e["seq"], "outcome": "DUPLICATE", "original_outcome": prior.outcome,
           "scan": dispatch.scan_view(db, sc, replay=True) if sc else None, "note": "Already synced"}
    if prior.outcome == "CONFLICT":   # the first response may never have reached the phone: repeat the physical instruction
        task = db.scalars(select(m.SegregationTask).where(m.SegregationTask.dedupe_key == f"offline:{e['client_event_id']}")).first()
        res |= {"note": task.reason if task else "Conflict recorded at first sync; a quality engineer must review this event",
                "segregation_task_id": task.id if task else None, "segregation_task_status": task.status if task else None}
    return res


def sync(db: Session, p: Principal, device_id: str, snapshot_id: str | None, events: list[dict]) -> dict:
    dev = _device(db, p, device_id, create=False)
    for e in events:
        if e.get("local_decision") not in LOCAL:
            raise ApiError(422, "VALIDATION", f"local_decision must be one of {sorted(LOCAL)}")
        parse_capture_time(e["scanned_at"])
        if e.get("chain_version", 1) == 1:
            e["snapshot_id"] = e.get("snapshot_id") or snapshot_id        # v1: the claim may come from the batch default
        # v2: the snapshot id is inside the hash; the event's own value (null for ONLINE) is the claim, never a default
    if snapshot_id:
        snap = db.get(m.OfflineSnapshot, snapshot_id)
        if not snap or snap.device_id != dev.id:
            raise ApiError(409, "SNAPSHOT_SCOPE_MISMATCH", "Snapshot was not issued to this device")
    old, new, broken = _verify_batch(dev.head_hash, dev.last_seq, events)
    results = []
    for e in old:   # idempotent replay: same content, same operator and the same snapshot claim as first time
        prior = db.get(m.DeviceEvent, (dev.id, e["seq"]))
        try:  # recompute from CONTENT: a changed event that reuses the old hash must not pass as a duplicate
            same = bool(prior) and prior.client_event_id == e["client_event_id"] and prior.operator_id == p.id \
                and e["prev_hash"] == prior.prev_hash and chain_hash(prior.prev_hash, e) == prior.hash == e["hash"]
        except CryptoError:
            same = False
        if not same:
            raise ApiError(409, "EVENT_CONTENT_CHANGED", f"Event seq {e['seq']} differs from the version already synced", {"seq": e["seq"]})
        # A recorded claim (including a known "no snapshot") must be repeated exactly. Pre-0006 rows have no reliable
        # claim metadata: only a non-null legacy value can be compared.
        if e.get("chain_version", 1) != prior.chain_version:
            raise ApiError(409, "EVENT_CONTENT_CHANGED", f"Event seq {e['seq']} was synced as chain v{prior.chain_version}",
                           {"seq": e["seq"], "field": "chain_version", "original": prior.chain_version})
        if (prior.claim_recorded or prior.claimed_snapshot_id is not None) and e.get("snapshot_id") != prior.claimed_snapshot_id:
            raise ApiError(409, "EVENT_CONTENT_CHANGED", f"Event seq {e['seq']} now cites a different snapshot than when it was synced",
                           {"seq": e["seq"], "field": "snapshot_id", "original": prior.claimed_snapshot_id})
        results.append(_replay_result(db, prior, e))
    if broken is not None:
        audit.append(db, p.username, "DEVICE_CHAIN_BROKEN", "device", dev.id, f"Chain broken at seq {broken}", data={"head_seq": dev.last_seq})
        raise_alert(db, type="OFFLINE_CHAIN_BROKEN", severity="HIGH", entity_type="DEVICE", entity_id=dev.id, reason_codes=["CHAIN_BROKEN"],
                    details={"broken_at": broken, "server_seq": dev.last_seq}, dedupe_key=f"chain:{dev.id}:{dev.last_seq}:{broken}")
        return {"results": [], "chain": {"ok": False, "broken_at": broken, "server_head": dev.head_hash, "server_seq": dev.last_seq, **_chain_policy(db)}}
    db.commit()  # release the device row; each new event commits in its own transaction below
    for e in new:
        results.append(_apply(p, dev.id, e))
    with session_factory()() as s2:
        d = s2.get(m.Device, dev.id)
        return {"results": results, "chain": {"ok": True, "server_head": d.head_hash, "server_seq": d.last_seq,
                                              "chain_version_high": d.chain_version_high, **_chain_policy(s2)}}


def _snapshot_reasons(db: Session, p: Principal, device_id: str, snap: m.OfflineSnapshot | None, claim: str | None,
                      shipment_id: str, scanned_at: datetime) -> tuple[list[dict], dict | None]:
    """(why a provisional load was not authorized by a valid snapshot of its own, the verified signed payload).
    The SIGNED payload is the authority: its device, operator and shipment binding, its allowlist and held list,
    and its expiry. The database row must agree with it."""
    if not claim:
        return [{"code": "SNAPSHOT_REQUIRED", "message": "Provisional load names no offline snapshot"}], None
    if not snap or snap.device_id != device_id or snap.operator_id != p.id or snap.shipment_id != shipment_id:
        return [{"code": "SNAPSHOT_SCOPE_MISMATCH", "message": "Snapshot was not issued to this device, operator and shipment"}], None
    try:
        signed = verify(snap.token, trusted_keys(db), "SNAP", int(snap.issued_at.timestamp())).payload
    except CryptoError as ce:
        return [{"code": "SNAPSHOT_SIGNATURE_INVALID", "message": f"Snapshot no longer verifies: {ce.code}"}], None
    if (signed.get("id"), signed.get("device_id"), signed.get("operator_id"), signed.get("shipment")) != (snap.id, device_id, p.id, shipment_id) \
            or int(signed.get("exp", 0)) != int(snap.expires_at.timestamp()):
        return [{"code": "SNAPSHOT_RECORD_MISMATCH", "message": "The stored snapshot record disagrees with its signed payload"}], None
    # Authority is judged at RECEIPT. Settlement attested an empty queue at a chain head, and recovery ended the
    # device's authority: an event arriving afterwards was not part of that history, whatever capture time it claims.
    now = _now()
    if snap.recovery_requested_at or snap.settlement == "RECOVERED":
        return [{"code": "SNAPSHOT_RECOVERED", "message": "This device was declared lost; its captures need physical verification"}], signed
    if snap.settled_at:
        return [{"code": "SNAPSHOT_SETTLED", "message": f"Received after the offline session was settled ({snap.settlement}, seq {snap.settled_seq}); "
                                                       "reconcile physically"}], signed
    if now.timestamp() >= int(signed["exp"]):
        return [{"code": "SNAPSHOT_EXPIRED_AT_RECEIPT", "message": "Received after the snapshot's signed expiry; a claimed capture time cannot grant load authority"}], signed
    if scanned_at > now + CLOCK_SKEW:
        return [{"code": "CAPTURE_TIME_INVALID", "message": "Capture time is in the future"}], signed
    if scanned_at < snap.issued_at - CLOCK_SKEW:
        return [{"code": "CAPTURE_BEFORE_SNAPSHOT", "message": "Captured before the snapshot was issued"}], signed
    return [], signed


def _version_reasons(dev: m.Device, version: int, floor: int) -> list[dict]:
    """A recorded event below the floor, or below what this device already wrote (an outdated tab sharing its queue),
    is kept for reconciliation but never confers load authority."""
    if version < floor:
        return [{"code": "CHAIN_VERSION_BELOW_FLOOR", "message": f"Captured by a v{version} writer; the plant requires v{floor} — update the scanner"}]
    if version < dev.chain_version_high:
        return [{"code": "CHAIN_VERSION_REGRESSION", "message": f"v{version} event after this device already wrote v{dev.chain_version_high} (outdated tab?)"}]
    return []


def _allowlist_reasons(scope: dict, pkg: m.Package | None) -> list[dict]:
    if pkg is None:
        return []   # the gate already blocks an unidentifiable label
    if pkg.id not in scope.get("allowed_packages", []):
        return [{"code": "NOT_IN_SNAPSHOT", "message": f"{pkg.label} was not on the signed snapshot's allowlist"}]
    if pkg.id in scope.get("held", []):
        return [{"code": "HELD_AT_SNAPSHOT", "message": f"{pkg.label} was held when the snapshot was issued"}]
    return []


def _provenance_conflict(db: Session, p: Principal, device_id: str, e: dict, prior: m.Scan, ship: m.Shipment | None, *, physical: bool) -> tuple[str, str | None]:
    """An existing scan owns this event id, but this capture did not produce it. Never adopt its decision.
    The shipment row is locked BEFORE anything is written, so a blocking task cannot slip past a concurrent departure."""
    if physical and ship:
        lock_set(db).lock(m.Shipment, [ship.id])
    note = ("This event's id is already used by a different recorded scan; it was not linked or loaded."
            + (" The crate may have been staged: physically verify and segregate it." if physical else " A quality engineer must review it."))
    audit.append(db, p.username, "DEVICE_EVENT_PROVENANCE_MISMATCH", "device", device_id, note,
                 data={"client_event_id": e["client_event_id"], "recorded_scan": prior.id, "seq": e["seq"], "local_decision": e["local_decision"]})
    raise_alert(db, type="OFFLINE_CONFLICT", severity="HIGH", entity_type="DEVICE", entity_id=device_id, reason_codes=["EVENT_PROVENANCE_MISMATCH"],
                details={"client_event_id": e["client_event_id"], "device_id": device_id, "recorded_scan": prior.id},
                dedupe_key=f"provenance:{e['client_event_id']}", audible=physical)
    task = segregation.create(db, type="SEGREGATION_REQUIRED", dedupe_key=f"offline:{e['client_event_id']}", shipment_id=ship.id if ship else None,
                              reason=note) if physical else None
    return note, task


def _apply(p: Principal, device_id: str, e: dict) -> dict:
    """One event = one transaction: gate replay + device_event + checkpoint advance."""
    with session_factory()() as db:
        dev = db.scalars(select(m.Device).where(m.Device.id == device_id).with_for_update().execution_options(populate_existing=True)).one()   # device row first
        if dev.last_seq != e["seq"] - 1 or dev.head_hash != e["prev_hash"]:
            raise ApiError(409, "SYNC_RACE", "Another sync advanced this device concurrently; resend from the server head")
        # Read the version floor under the policy lock (shared): a floor change waits for this decision, never splits it.
        db.execute(text("SELECT pg_advisory_xact_lock_shared(:k)"), {"k": policies.POLICY_LOCK})
        version = e.get("chain_version", 1)
        below_floor = _version_reasons(dev, version, policies.settings_of(policies.active(db)).offline.chain_min_version)
        scanned_at = parse_capture_time(e["scanned_at"])
        claim = e.get("snapshot_id")
        snap = db.get(m.OfflineSnapshot, claim) if claim else None
        ship = db.get(m.Shipment, e["shipment_id"])
        ld = e["local_decision"]
        view, note, outcome, task, original = None, "", None, None, None
        prior = db.scalars(select(m.Scan).where(m.Scan.client_event_id == e["client_event_id"])).first()
        if ld == "ONLINE" and prior and ship and prior.device_id == device_id and prior.actor_id == p.id and not prior.offline \
                and prior.content_sha256 == dispatch.content_sha(ship.id, e["token"]):
            view, outcome, original = dispatch.scan_view(db, prior, replay=True), "DUPLICATE", prior.decision
            note = f"Online decision recovered: {prior.decision}"
        elif prior:
            outcome = "CONFLICT"
            note, task = _provenance_conflict(db, p, device_id, e, prior, ship, physical=ld == "PROVISIONAL_ACCEPT")
        elif ld == "ONLINE":
            view, _ = dispatch.scan(db, p, e["shipment_id"], e["token"], e["client_event_id"], device_id, offline=True, captured_at=scanned_at, commit_load=False)
            outcome, note = "BLOCKED", "Online request never reached the server — recorded, not loaded"
        elif ld == "LOCAL_BLOCK":
            view, _ = dispatch.scan(db, p, e["shipment_id"], e["token"], e["client_event_id"], device_id, offline=True, captured_at=scanned_at, commit_load=False)
            outcome, note = "BLOCKED", "Blocked on device — recorded, not loaded"
        else:
            untrusted, scope = _snapshot_reasons(db, p, device_id, snap, claim, ship.id if ship else e["shipment_id"], scanned_at)
            untrusted = below_floor + untrusted
            view, _ = dispatch.scan(db, p, e["shipment_id"], e["token"], e["client_event_id"], device_id, offline=True, captured_at=scanned_at,
                                    authorize=lambda pkg: untrusted or _allowlist_reasons(scope, pkg))
            if view["decision"] == "ACCEPTED":
                outcome = "ACCEPTED"
            else:
                outcome = "CONFLICT"
                codes = [r["code"] for r in view["reasons"]]
                label = view.get("package_label") or "unidentified package"
                note = f"Server refused the provisional load ({', '.join(codes)}): physically unload and segregate {label}"
                task = segregation.create(db, type="SEGREGATION_REQUIRED", dedupe_key=f"offline:{e['client_event_id']}", scan_id=view["scan_id"],
                                          package_id=view.get("package_id"), shipment_id=view["shipment_id"], reason=note)
                raise_alert(db, type="OFFLINE_CONFLICT", severity="HIGH", entity_type="SHIPMENT", entity_id=view["shipment_id"], reason_codes=codes,
                            details={"client_event_id": e["client_event_id"], "device_id": device_id, "package": label, "task_id": task},
                            dedupe_key=f"offline-conflict:{e['client_event_id']}", audible=True)
        valid_assoc = snap is not None and snap.device_id == device_id and snap.operator_id == p.id
        db.add(m.DeviceEvent(device_id=device_id, seq=e["seq"], client_event_id=e["client_event_id"], prev_hash=e["prev_hash"], hash=e["hash"],
                             snapshot_id=snap.id if valid_assoc else None, claimed_snapshot_id=claim, claim_recorded=True, chain_version=version, scan_id=view["scan_id"] if view else None,
                             local_decision=ld, outcome=outcome, scanned_at=scanned_at, operator_id=p.id))
        dev.last_seq, dev.head_hash = e["seq"], e["hash"]
        dev.chain_version_high = max(dev.chain_version_high, version)
        db.commit()
        out = {"client_event_id": e["client_event_id"], "seq": e["seq"], "outcome": outcome, "scan": view, "note": note, "segregation_task_id": task,
               "chain_version": version}
        if original:
            out["original_outcome"] = original
        return out
