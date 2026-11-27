"""Release blockers from Astra's independent verification of the 769c003/5dfad85 candidate.
Each test reproduces one reported finding and asserts it now fails closed. Real PostgreSQL."""
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update

from app.db import models as m
from app.features.dispatch import segregation
from tests.test_dispatch import scan, setup
from tests.test_offline_release_blockers import blockers, close, loaded_online, recover, upload
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


# ── 3. unrelated or synthetic evidence settling a lost-device recovery ─────────────────────────────────────────
def test_recovery_evidence_must_be_requested_first_physical_recent_and_unused(client, auth, db):
    *_, shp = loaded_online(client, auth, db)
    sid = snap(client, auth, shp["id"]).json()["snapshot_id"]
    old = upload(client, auth)                                                              # stored BEFORE the recovery
    r = recover(client, auth, sid, evidence=[old])
    assert r.status_code == 409 and r.json()["error"]["code"] == "RECOVERY_NOT_REQUESTED"
    assert recover(client, auth, sid).json()["recovery_requested_at"]
    r = recover(client, auth, sid, evidence=[old])
    assert r.status_code == 422 and r.json()["error"]["code"] == "EVIDENCE_NOT_LINKED"
    # Unrelated SYNTHETIC_TEST evidence (e.g. a seal image) never settles a recovery, whenever it was stored.
    synth = db.scalars(select(m.Evidence.sha256).where(m.Evidence.evidence_source == "SYNTHETIC_TEST")).first()
    assert synth
    db.execute(update(m.Evidence).where(m.Evidence.sha256 == synth).values(created_at=datetime.now(UTC) + timedelta(seconds=1))); db.commit()
    r = recover(client, auth, sid, evidence=[synth])
    assert r.status_code == 422 and "SYNTHETIC_TEST" in r.json()["error"]["message"]
    sn = db.get(m.OfflineSnapshot, sid); db.refresh(sn)
    assert sn.settled_at is None
    # A record uploaded for this recovery settles it.
    db.execute(update(m.Evidence).where(m.Evidence.sha256 == old).values(created_at=datetime.now(UTC) + timedelta(seconds=1))); db.commit()
    rec = recover(client, auth, sid, evidence=[old]).json()
    assert rec["settlement"] == "RECOVERED" and rec["recovery_evidence"][0]["for_snapshot"] == sid
    assert rec["recovery_evidence"][0]["uploaded_by"] == "qe.ananya"


def test_one_record_cannot_settle_two_recoveries(client, auth, db):
    *_, shp = loaded_online(client, auth, db)
    s1 = snap(client, auth, shp["id"], device="dev-a1").json()["snapshot_id"]
    s2 = snap(client, auth, shp["id"], device="dev-b2").json()["snapshot_id"]
    recover(client, auth, s1); recover(client, auth, s2)
    sha = upload(client, auth)
    db.execute(update(m.Evidence).where(m.Evidence.sha256 == sha).values(created_at=datetime.now(UTC) + timedelta(seconds=1))); db.commit()
    assert recover(client, auth, s1, evidence=[sha]).json()["settlement"] == "RECOVERED"
    r = recover(client, auth, s2, evidence=[sha])
    assert r.status_code == 422 and "already settled" in r.json()["error"]["message"]


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
