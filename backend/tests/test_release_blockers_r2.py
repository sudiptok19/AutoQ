"""Release blockers from Astra's independent verification of the 769c003/5dfad85 candidate.
Each test reproduces one reported finding and asserts it now fails closed. Real PostgreSQL."""
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update

from app.db import models as m
from app.features.dispatch import segregation
from tests.test_dispatch import scan, setup
from tests.test_offline_release_blockers import _race, _waiting_on_locks, attach, blockers, close, loaded_online, manifest_labels, record_pdf, recover, settle
from tests.test_offline_scanner import GEN, events, snap, sync
from tests.test_spc_review import drifting_lot, review  # noqa: F401  (fixture)

API = "/api/v1"


def depart(client, auth, shp_id):
    return client.post(f"{API}/shipments/{shp_id}/events", json={"type": "DEPARTED", "location": "Plant gate"}, headers=auth("disp.kiran"))


def closed_shipment(client, auth, db):
    _, _, order, a, b, shp = setup(client, auth, db)
    scan(client, auth, shp["id"], a["token"], "on-1"); scan(client, auth, shp["id"], b["token"], "on-2")
    assert close(client, auth, shp["id"]).json()["status"] == "CLOSED"
    return a, b, shp


# ── 1. departure after the loads were removed ──────────────────────────────────────────────────────────────────
def test_removing_a_crate_from_a_closed_manifest_reopens_it_and_departure_rechecks_the_manifest(client, auth, db):
    a, b, shp = closed_shipment(client, auth, db)
    for k in (a, b):
        tid = segregation.create(db, type="REMOVAL_REQUIRED", dedupe_key=f"t:{k['id']}", reason="test removal", package_id=k["id"], shipment_id=shp["id"])
        db.commit()
        r = client.post(f"{API}/segregation-tasks/{tid}/complete", json={"reason": "Crate pulled from the truck"}, headers=auth("disp.kiran"))
        assert r.status_code == 200, r.text
    s = db.get(m.Shipment, shp["id"]); db.refresh(s)
    assert s.status == "OPEN" and s.closed_at is None                                      # the closed manifest no longer described the truck
    assert db.scalars(select(m.AuditEvent).where(m.AuditEvent.action == "MANIFEST_REOPENED")).first() is not None
    assert depart(client, auth, shp["id"]).json()["error"]["code"] == "MANIFEST_OPEN"
    assert "EMPTY_MANIFEST" in blockers(close(client, auth, shp["id"]))


def test_departure_itself_refuses_an_empty_manifest(client, auth, db):
    a, b, shp = closed_shipment(client, auth, db)
    # Defense in depth: even if loads vanished from a CLOSED shipment by some other path, departure re-checks.
    db.execute(update(m.Load).where(m.Load.shipment_id == shp["id"]).values(removed_at=datetime.now(UTC), removed_reason="test")); db.commit()
    r = depart(client, auth, shp["id"])
    assert r.status_code == 409 and "EMPTY_MANIFEST" in blockers(r)
    assert db.get(m.Shipment, shp["id"]).status == "CLOSED"


# ── 2. departure with an expired seal recheck ──────────────────────────────────────────────────────────────────
def _age_rechecks(db, shp_id, minutes):
    pkg_ids = list(db.scalars(select(m.Load.package_id).where(m.Load.shipment_id == shp_id, m.Load.removed_at.is_(None))))
    for kind, extra in (("ENROLL", 5), ("RECHECK", 0)):   # keep the order: enrolment, then the (now old) recheck
        db.execute(update(m.SealCheck).where(m.SealCheck.package_id.in_(pkg_ids), m.SealCheck.kind == kind)
                   .values(created_at=datetime.now(UTC) - timedelta(minutes=minutes + extra)))
    db.commit()


def test_departure_requires_a_current_seal_recheck_without_ordering_an_unload(client, auth, db):
    a, b, shp = closed_shipment(client, auth, db)
    _age_rechecks(db, shp["id"], 90)
    r = depart(client, auth, shp["id"])
    assert r.status_code == 409 and r.json()["error"]["code"] == "DEPARTURE_NOT_RECONCILED"
    assert set(blockers(r)) == {"SEAL_RECHECK_EXPIRED"}
    s = db.get(m.Shipment, shp["id"]); db.refresh(s)
    assert s.status == "CLOSED"                                                             # nothing to unload: recheck on the truck
    assert db.scalars(select(m.SegregationTask).where(m.SegregationTask.shipment_id == shp["id"])).first() is None


def test_close_also_requires_a_current_seal_recheck(client, auth, db):
    _, _, order, a, b, shp = setup(client, auth, db)
    scan(client, auth, shp["id"], a["token"], "on-1"); scan(client, auth, shp["id"], b["token"], "on-2")
    _age_rechecks(db, shp["id"], 90)
    assert blockers(close(client, auth, shp["id"])) == ["SEAL_RECHECK_EXPIRED", "SEAL_RECHECK_EXPIRED"]


# ── 3. recovery evidence: bound to one pending recovery, attested, consumed once ───────────────────────────────
def two_pending_recoveries(client, auth, db):
    """Two shipments on one order, each with a requested recovery (its own device, snapshot and task)."""
    _, _, order, _, _, ship = loaded_online(client, auth, db)
    other = client.post(f"{API}/shipments", headers=auth("disp.kiran"), json={"order_id": order["id"], "vehicle_no": "KA-01-R2", "dock": "Dock B"})
    assert other.status_code == 201, other.json()
    out = []
    for shp, device in ((ship, "dev-a1"), (other.json(), "dev-b2")):
        sid = snap(client, auth, shp["id"], device=device).json()["snapshot_id"]
        pending = recover(client, auth, sid).json()
        assert pending["settlement"] is None and pending["recovery_task_id"]
        out.append((shp, sid, pending["recovery_task_id"]))
    return out


def _still_pending(db, sid, task_id):
    db.expire_all()
    assert db.get(m.OfflineSnapshot, sid).settled_at is None
    assert db.get(m.SegregationTask, task_id).status == "OPEN"


def test_generic_uploads_never_settle_a_recovery(client, auth, db):
    (shp, sid, task), _ = two_pending_recoveries(client, auth, db)
    synth = db.scalars(select(m.Evidence.sha256).where(m.Evidence.evidence_source == "SYNTHETIC_TEST")).first()
    for who in ("qe.ananya", "qm.farhan"):
        for purpose in ("REVIEW_RECORD", "SUPPLIER_DOCUMENT"):
            sha = client.post(f"{API}/evidence", headers=auth(who), data={"purpose": purpose},
                              files={"file": ("x.pdf", record_pdf(f"{who}{purpose}"), "application/pdf")}).json()["sha256"]
            r = recover(client, auth, sid, evidence=[sha])                      # fresh, physical source, QE/QM, after the request
            assert r.status_code == 422 and r.json()["error"]["code"] == "EVIDENCE_NOT_LINKED"
    assert recover(client, auth, sid, evidence=[synth]).status_code == 422
    _still_pending(db, sid, task)
    assert set(blockers(close(client, auth, shp["id"]))) == {"SNAPSHOT_UNSETTLED", "PHYSICAL_TASK_OPEN"}


def test_record_needs_a_requested_recovery_a_quality_attester_and_a_complete_count(client, auth, db):
    *_, shp = loaded_online(client, auth, db)
    sid = snap(client, auth, shp["id"]).json()["snapshot_id"]
    manifest = manifest_labels(db, shp["id"])
    r = attach(client, auth, sid, counted=manifest)
    assert r.status_code == 409 and r.json()["error"]["code"] == "RECOVERY_NOT_REQUESTED"
    task = recover(client, auth, sid).json()["recovery_task_id"]
    assert attach(client, auth, sid, counted=manifest, who="disp.kiran").status_code == 403
    r = attach(client, auth, sid, counted=manifest[:1])
    assert r.status_code == 422 and r.json()["error"]["code"] == "COUNT_MISMATCH" and r.json()["error"]["details"]["missing"] == manifest[1:]
    r = attach(client, auth, sid, counted=manifest, locations=["TRUCK"])
    assert r.status_code == 422 and r.json()["error"]["code"] == "RECONCILIATION_SCOPE_INCOMPLETE"
    assert attach(client, auth, sid, counted=manifest[:1], disposition="DISCREPANCY_SEGREGATED").status_code == 422   # needs what was done
    earlier = client.post(f"{API}/evidence", headers=auth("qe.ananya"), data={"purpose": "REVIEW_RECORD"},
                          files={"file": ("x.pdf", data := record_pdf("old"), "application/pdf")})
    assert earlier.status_code == 201
    r = attach(client, auth, sid, counted=manifest, data=data)                     # bytes stored before: not made for this recovery
    assert r.status_code == 422 and r.json()["error"]["code"] == "EVIDENCE_NOT_LINKED"
    _still_pending(db, sid, task)
    assert db.scalar(select(m.RecoveryRecord.sha256).where(m.RecoveryRecord.snapshot_id == sid)) is None   # refusals roll back whole


def test_bound_record_settles_its_own_recovery_once(client, auth, db):
    (shp, sid, task), (other, other_sid, other_task) = two_pending_recoveries(client, auth, db)
    assert close(client, auth, shp["id"]).status_code == 409
    data = record_pdf("A")
    r = attach(client, auth, sid, counted=manifest_labels(db, shp["id"]), data=data)
    assert r.status_code == 200, r.json()
    rec = r.json()["recovery_evidence"][0]
    assert r.json()["settlement"] == "RECOVERED" and rec["for_snapshot"] == sid and rec["recovery_task_id"] == task
    assert rec["shipment_id"] == shp["id"] and rec["uploaded_by"] == "qe.ananya" and rec["counted_scope"]["locations"] == ["STAGING_LANE", "TRUCK"]
    row = db.get(m.RecoveryRecord, rec["sha256"])
    assert (row.snapshot_id, row.task_id, row.shipment_id, row.disposition) == (sid, task, shp["id"], "MATCHES_MANIFEST")
    db.expire_all()
    assert db.get(m.SegregationTask, task).status == "COMPLETED"
    # the same record cannot settle the other shipment's recovery, by either route
    r = attach(client, auth, other_sid, counted=[], data=data)
    assert r.status_code == 422 and r.json()["error"]["code"] == "EVIDENCE_NOT_LINKED"
    r = recover(client, auth, other_sid, evidence=[rec["sha256"]])
    assert r.status_code == 422 and sid in r.json()["error"]["message"]
    _still_pending(db, other_sid, other_task)
    assert close(client, auth, shp["id"]).status_code == 200
    # a discrepancy is settled only with what was done, and is recorded as such
    r = attach(client, auth, other_sid, counted=["PKG-STRAY"], disposition="DISCREPANCY_SEGREGATED", note="Stray crate pulled to cage Q2", who="qm.farhan")
    assert r.status_code == 200 and r.json()["recovery_evidence"][0]["counted_scope"]["packages"] == ["PKG-STRAY"]


def test_one_record_has_one_consumer_under_concurrency(client, auth, db, monkeypatch):
    import concurrent.futures as cf
    from threading import Barrier
    from app.features.offline_scanner import service
    (shp, sid, task), (other, other_sid, other_task) = two_pending_recoveries(client, auth, db)
    gate, original = Barrier(2, timeout=10), service.sniff

    def both_checked(data):           # both requests have passed every read-side check before either writes
        gate.wait()
        return original(data)

    monkeypatch.setattr(service, "sniff", both_checked)
    data = record_pdf("shared")
    auth("qe.ananya")
    with cf.ThreadPoolExecutor(2) as ex:
        futs = [ex.submit(attach, client, auth, s_, counted=c, data=data, disposition=d, note=n)
                for s_, c, d, n in ((sid, manifest_labels(db, shp["id"]), "MATCHES_MANIFEST", ""),
                                    (other_sid, ["X"], "DISCREPANCY_SEGREGATED", "Recount of the second truck"))]
        codes = sorted(f.result(timeout=30).status_code for f in futs)
    assert codes == [200, 422]
    db.expire_all()
    settled = [x for x in (sid, other_sid) if db.get(m.OfflineSnapshot, x).settlement == "RECOVERED"]
    assert len(settled) == 1 and db.scalar(select(m.RecoveryRecord.snapshot_id)) == settled[0]


def test_task_completion_still_settles_with_an_attributable_disposition(client, auth, db):
    *_, shp = loaded_online(client, auth, db)
    sid = snap(client, auth, shp["id"]).json()["snapshot_id"]
    task = recover(client, auth, sid).json()["recovery_task_id"]
    r = client.post(f"{API}/segregation-tasks/{task}/complete", json={"reason": "Counted truck and staging: matches the manifest"}, headers=auth("qe.ananya"))
    assert r.status_code == 200
    db.expire_all()
    sn = db.get(m.OfflineSnapshot, sid)
    assert sn.settlement == "RECOVERED" and db.get(m.User, sn.settled_by).username == "qe.ananya"


# ── 4. a refresh superseding a pending recovery ────────────────────────────────────────────────────────────────
def test_an_empty_queue_refresh_never_supersedes_a_pending_recovery(client, auth, db):
    *_, shp = loaded_online(client, auth, db)
    sid = snap(client, auth, shp["id"]).json()["snapshot_id"]
    assert recover(client, auth, sid).json()["recovery_requested_at"]
    r = client.get(f"{API}/offline/snapshot", params={"shipment_id": shp["id"], "device_id": "dev-a1", "local_seq": 0, "local_head": GEN, "pending": 0},
                   headers=auth("disp.kiran")).json()
    assert r["superseded"] == []
    sn = db.get(m.OfflineSnapshot, sid); db.refresh(sn)
    assert sn.settled_at is None and sn.recovery_requested_at is not None
    assert "SNAPSHOT_UNSETTLED" in blockers(close(client, auth, shp["id"]))


# ── 5. missing vs explicit null at sync ingress ────────────────────────────────────────────────────────────────
def test_v2_event_without_a_snapshot_field_is_rejected_not_read_as_null(client, auth, db):
    _, _, _, a, _, shp = setup(client, auth, db)
    ev = {"client_event_id": "dev-a1:000001", "seq": 1, "token": a["token"], "shipment_id": shp["id"],
          "scanned_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"), "local_decision": "ONLINE", "chain_version": 2,
          "prev_hash": GEN, "hash": "a" * 64}
    snap(client, auth, shp["id"])
    r = sync(client, auth, "dev-a1", None, [ev])
    assert r.status_code == 422 and r.json()["error"]["details"]["field"] == "snapshot_id"
    assert db.get(m.DeviceEvent, ("dev-a1", 1)) is None


def test_v1_explicit_null_is_not_replaced_by_the_batch_snapshot(client, auth, db):
    _, _, _, a, _, shp = setup(client, auth, db)
    sid = snap(client, auth, shp["id"]).json()["snapshot_id"]
    evs = events("dev-a1", [(a["token"], shp["id"], "ONLINE")])
    evs[0]["snapshot_id"] = None                                                            # explicit: no snapshot
    assert sync(client, auth, "dev-a1", sid, evs).status_code == 200
    de = db.get(m.DeviceEvent, ("dev-a1", 1))
    assert de.claimed_snapshot_id is None and de.snapshot_id is None


# ── 6. cross-shipment snapshot claim persisted as a validated association ──────────────────────────────────────
def test_a_claim_for_another_shipments_snapshot_is_kept_verbatim_but_never_associated(client, auth, db):
    _, _, order, a, b, shp = setup(client, auth, db)
    other = client.post(f"{API}/shipments", json={"order_id": order["id"], "vehicle_no": "WB-04-ZZ-0001", "dock": "Dock 4"}, headers=auth("disp.kiran")).json()
    foreign = snap(client, auth, other["id"]).json()["snapshot_id"]                        # same device and operator, other shipment
    r = sync(client, auth, "dev-a1", None, [{**e, "snapshot_id": foreign} for e in events("dev-a1", [(a["token"], shp["id"], "PROVISIONAL_ACCEPT")])])
    res = r.json()["results"][0]
    assert res["outcome"] == "CONFLICT" and "SNAPSHOT_SCOPE_MISMATCH" in res["note"]
    de = db.get(m.DeviceEvent, ("dev-a1", 1))
    assert de.claimed_snapshot_id == foreign and de.snapshot_id is None


# ── 7. SPC review linked to an unrelated alert ─────────────────────────────────────────────────────────────────
def test_spc_review_links_only_the_drift_warning_for_its_own_inspection(client, auth, db, drifting_lot):
    _, _, insps = drifting_lot
    warn = db.scalars(select(m.SpcWarning)).first()
    assert warn is not None
    unrelated = db.scalars(select(m.Alert).where(m.Alert.id != warn.alert_id)).first() or \
        m.Alert(id="alrt_unrelated", type="QUARANTINE", severity="HIGH", entity_type="LOT", entity_id="x", dedupe_key="unrelated")
    if unrelated.id == "alrt_unrelated":
        db.add(unrelated); db.commit()
    r = client.post(f"{API}/spc-reviews", json={"inspection_id": warn.inspection_id, "alert_id": unrelated.id}, headers=auth("qe.ananya"))
    assert r.status_code == 422 and r.json()["error"]["code"] == "ALERT_NOT_LINKED"
    other_insp = next(i["id"] for i in insps if i["id"] != warn.inspection_id)
    r = client.post(f"{API}/spc-reviews", json={"inspection_id": other_insp, "alert_id": warn.alert_id}, headers=auth("qe.ananya"))
    assert r.status_code == 422 and r.json()["error"]["code"] == "ALERT_NOT_LINKED"
    ok = client.post(f"{API}/spc-reviews", json={"inspection_id": warn.inspection_id, "alert_id": warn.alert_id}, headers=auth("qe.ananya"))
    assert ok.status_code in (200, 201) and ok.json()["alert_id"] == warn.alert_id


# ── 8. version floor downgrade after cutover ───────────────────────────────────────────────────────────────────
def test_the_chain_floor_cannot_be_lowered_after_cutover(client, auth, db):
    from tests.test_chain_v2 import set_chain_policy
    set_chain_policy(db, 2, 2)
    current = client.get(f"{API}/policies", headers=auth("admin")).json()["settings"]
    current["offline"]["chain_min_version"] = 1
    r = client.put(f"{API}/policies", headers=auth("admin"), json=current)
    assert r.status_code == 409 and r.json()["error"]["code"] == "POLICY_FLOOR_DOWNGRADE"
    assert client.get(f"{API}/policies", headers=auth("admin")).json()["settings"]["offline"]["chain_min_version"] == 2


# ── 9. legacy queue drained after the writer switch ────────────────────────────────────────────────────────────
def test_a_v1_provisional_event_first_received_after_the_writer_switch_cannot_load(client, auth, db):
    from tests.test_chain_v2 import ev, set_chain_policy
    _, _, _, a, _, shp = setup(client, auth, db)
    sid = snap(client, auth, shp["id"]).json()["snapshot_id"]
    set_chain_policy(db, 2, 1)
    queued = ev("dev-a1", 1, GEN, a["token"], shp["id"], "PROVISIONAL_ACCEPT", snapshot_id=sid)
    res = sync(client, auth, "dev-a1", sid, [queued]).json()
    assert res["chain"]["server_seq"] == 1
    r = res["results"][0]
    assert r["outcome"] == "CONFLICT" and r["segregation_task_id"] and "CHAIN_VERSION_BELOW_WRITER" in r["note"]
    assert db.scalar(select(m.Load.id)) is None
    again = sync(client, auth, "dev-a1", sid, [queued]).json()["results"][0]                 # exact historical replay
    assert again["outcome"] == "DUPLICATE" and again["original_outcome"] == "CONFLICT"


# ── R2 review: concurrent scan provenance (both commit orders) ────────────────────────────────────────────────
def test_an_online_scan_that_wins_the_event_id_is_not_adopted_by_offline_reconciliation(client, auth, db, monkeypatch):
    from threading import Event
    from app.features.offline_scanner import service
    from tests.test_chain_v2 import ev
    _, _, _, pkg, _, ship = setup(client, auth, db)
    sid = snap(client, auth, ship["id"]).json()["snapshot_id"]
    assert settle(client, auth, sid).status_code == 200                                # offline authority surrendered
    capture = ev("dev-a1", 1, GEN, pkg["token"], ship["id"], "PROVISIONAL_ACCEPT", version=2, snapshot_id=sid)
    reached, release, original = Event(), Event(), service._snapshot_reasons

    def pause(*args):                 # after the sync's provenance pre-read, before its gate replay
        reached.set()
        assert release.wait(15)
        return original(*args)

    monkeypatch.setattr(service, "_snapshot_reasons", pause)
    auth("disp.kiran")
    import concurrent.futures as cf
    with cf.ThreadPoolExecutor(1) as ex:
        pending = ex.submit(sync, client, auth, "dev-a1", None, [capture])
        try:
            assert reached.wait(10)
            online = scan(client, auth, ship["id"], pkg["token"], capture["client_event_id"])
            assert online.json()["decision"] == "ACCEPTED"
        finally:
            release.set()
        r = pending.result(timeout=20).json()["results"][0]
    assert r["outcome"] == "CONFLICT" and r["segregation_task_id"], r
    db.expire_all()
    assert db.get(m.DeviceEvent, ("dev-a1", 1)).scan_id is None
    loads = db.scalars(select(m.Load).where(m.Load.shipment_id == ship["id"], m.Load.removed_at.is_(None))).all()
    assert [x.accepted_scan_id for x in loads] == [online.json()["scan_id"]]


def test_an_online_request_cannot_inherit_the_offline_scan_that_won_first(client, auth, db, monkeypatch):
    from threading import Event
    from app.features.dispatch import service
    from tests.test_chain_v2 import ev
    _, _, _, pkg, _, ship = setup(client, auth, db)
    sid = snap(client, auth, ship["id"]).json()["snapshot_id"]
    capture = ev("dev-a1", 1, GEN, pkg["token"], ship["id"], "PROVISIONAL_ACCEPT", version=2, snapshot_id=sid)
    reached, release, original = Event(), Event(), service.evaluate

    def pause_first(*args):
        if not reached.is_set():
            reached.set()
            assert release.wait(15)
        return original(*args)

    monkeypatch.setattr(service, "evaluate", pause_first)
    auth("disp.kiran")
    import concurrent.futures as cf
    with cf.ThreadPoolExecutor(1) as ex:
        pending = ex.submit(scan, client, auth, ship["id"], pkg["token"], capture["client_event_id"])
        try:
            assert reached.wait(10)
            assert sync(client, auth, "dev-a1", None, [capture]).json()["results"][0]["outcome"] == "ACCEPTED"
        finally:
            release.set()
        competing = pending.result(timeout=20)
    assert competing.status_code == 409 and competing.json()["error"]["code"] == "EVENT_PROVENANCE_MISMATCH"
    # the device's own online retry still replays
    assert scan(client, auth, ship["id"], pkg["token"], "own-online").json()["decision"] in ("ACCEPTED", "BLOCKED")
    again = scan(client, auth, ship["id"], pkg["token"], "own-online")
    assert again.status_code == 200 and again.json()["replay"] is True


# ── R2 review: a late conflict after departure gets an instruction for where the truck is ─────────────────────
def test_late_conflict_after_departure_gets_a_carrier_instruction_that_replays(client, auth, db, monkeypatch):
    from app.features.offline_scanner import service
    captures, apply_event = [], service._apply

    def record(principal, device_id, event):
        captures.append((device_id, dict(event)))
        return apply_event(principal, device_id, event)

    monkeypatch.setattr(service, "_apply", record)
    out, ship = _race(client, auth, db, first="depart")
    assert ship.status == "IN_TRANSIT"
    conflict = out["sync"].json()["results"][0]
    task = db.get(m.SegregationTask, conflict["segregation_task_id"])
    assert task.type == "IN_TRANSIT_RECALL" and task.shipment_id == ship.id
    assert "unload" not in task.reason.lower() and "carrier" in task.reason and task.consignee_action["vehicle_no"] == ship.vehicle_no
    device_id, event = captures[0]
    replay = sync(client, auth, device_id, None, [event]).json()["results"][0]
    assert replay["segregation_task_id"] == task.id and replay["note"] == conflict["note"]
    assert db.scalar(select(m.SegregationTask.id).where(m.SegregationTask.dedupe_key == f"offline:{event['client_event_id']}",
                                                        m.SegregationTask.id != task.id)) is None


# ── R2 review: historical replay compatibility and lost responses ──────────────────────────────────────────────
def test_replay_preserves_a_pre_r2_v1_effective_batch_claim(client, auth, db):
    from tests.test_chain_v2 import ev
    _, _, _, pkg, _, ship = setup(client, auth, db)
    sid = snap(client, auth, ship["id"]).json()["snapshot_id"]
    wire = ev("dev-a1", 1, GEN, pkg["token"], ship["id"], "ONLINE")
    wire["snapshot_id"] = None                                              # the phone sent an explicit null …
    first = sync(client, auth, "dev-a1", sid, [{**wire, "snapshot_id": sid}])   # … which pre-R2 stored as the batch claim
    assert first.status_code == 200 and db.get(m.DeviceEvent, ("dev-a1", 1)).claimed_snapshot_id == sid
    r = sync(client, auth, "dev-a1", sid, [wire])
    assert r.status_code == 200 and r.json()["results"][0]["outcome"] == "DUPLICATE"
    assert r.json()["results"][0]["original_outcome"] == first.json()["results"][0]["outcome"]
    # without that batch value, or under another batch, the stored claim is not matched
    assert sync(client, auth, "dev-a1", None, [wire]).status_code == 409


def test_a_new_v1_explicit_null_never_borrows_the_batch_snapshot(client, auth, db):
    from tests.test_chain_v2 import ev
    _, _, _, pkg, _, ship = setup(client, auth, db)
    sid = snap(client, auth, ship["id"]).json()["snapshot_id"]
    e = ev("dev-a1", 1, GEN, pkg["token"], ship["id"], "PROVISIONAL_ACCEPT")
    e["snapshot_id"] = None
    r = sync(client, auth, "dev-a1", sid, [e]).json()["results"][0]
    assert r["outcome"] == "CONFLICT" and "SNAPSHOT_REQUIRED" in r["note"]
    assert db.get(m.DeviceEvent, ("dev-a1", 1)).claimed_snapshot_id is None


def test_losing_both_online_and_first_sync_replies_keeps_the_online_decision(client, auth, db):
    from tests.test_chain_v2 import ev
    _, _, _, pkg, _, ship = setup(client, auth, db)
    assert snap(client, auth, ship["id"]).status_code == 200
    capture = ev("dev-a1", 1, GEN, pkg["token"], ship["id"], "ONLINE", version=2)
    online = client.post(f"{API}/shipments/{ship['id']}/scans", headers=auth("disp.kiran"),
                         json={"token": pkg["token"], "client_event_id": capture["client_event_id"], "device_id": "dev-a1"}).json()
    assert online["decision"] == "ACCEPTED"
    for _ in range(3):
        r = sync(client, auth, "dev-a1", None, [capture]).json()["results"][0]
        assert r["outcome"] == "DUPLICATE" and r["original_outcome"] == "ACCEPTED" and r["scan"]["scan_id"] == online["scan_id"], r


def test_sync_rejects_coerced_json_types(client, auth, db):
    from tests.test_chain_v2 import ev
    _, _, _, pkg, _, ship = setup(client, auth, db)
    snap(client, auth, ship["id"])
    good = ev("dev-a1", 1, GEN, pkg["token"], ship["id"], "ONLINE", version=2)
    for field, bad in (("seq", "1"), ("chain_version", "2"), ("chain_version", True), ("seq", 1.0), ("token", 5)):
        r = sync(client, auth, "dev-a1", None, [{**good, field: bad}])
        assert r.status_code == 422, (field, bad, r.status_code)
    assert db.get(m.DeviceEvent, ("dev-a1", 1)) is None
