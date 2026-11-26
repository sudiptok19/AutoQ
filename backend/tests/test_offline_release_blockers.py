"""Offline dispatch release blockers (review of 45a6d3f): settlement per snapshot, replay authorization and provenance,
shipment-safe segregation. Real PostgreSQL, separate connections for the races."""
import concurrent.futures as cf
import time
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, text, update

from app.db import models as m
from app.db.session import session_factory
from app.features.dispatch import segregation
from app.features.offline_scanner.service import _allowlist_reasons
from tests.test_dispatch import scan, sealed, setup
from tests.test_offline_scanner import GEN, events, snap, sync

API = "/api/v1"


def _count(db, model, *where):
    return db.scalar(select(func.count()).select_from(model).where(*where))


def close(client, auth, shp_id, who="disp.kiran"):
    return client.post(f"{API}/shipments/{shp_id}/close", headers=auth(who))


def blockers(r):
    return [b["code"] for b in r.json()["error"]["details"]["blockers"]]


def settle(client, auth, sid, device="dev-a1", seq=0, pending=0, head=GEN, who="disp.kiran"):
    return client.post(f"{API}/offline/snapshots/{sid}/settle", json={"device_id": device, "local_seq": seq, "local_head": head, "pending": pending},
                       headers=auth(who))


def recover(client, auth, sid, who="qm.farhan", reason="Phone dropped in the yard; not recoverable today", evidence=None):
    return client.post(f"{API}/offline/snapshots/{sid}/recover", json={"reason": reason, "evidence_ids": evidence or []}, headers=auth(who))


def loaded_online(client, auth, db):
    """Shipment fully loaded online (a + b), ready to close but for offline authorizations."""
    c, units, order, a, b, shp = setup(client, auth, db)
    scan(client, auth, shp["id"], a["token"], "on-1"); scan(client, auth, shp["id"], b["token"], "on-2")
    return c, units, order, a, b, shp


# ── 1. settlement per device × shipment × snapshot ─────────────────────────────────────────────────────────────
def test_close_waits_for_an_explicit_settlement_with_an_empty_queue(client, auth, db):
    *_, shp = loaded_online(client, auth, db)
    sid = snap(client, auth, shp["id"]).json()["snapshot_id"]
    assert blockers(close(client, auth, shp["id"])) == ["SNAPSHOT_UNSETTLED"]
    r = settle(client, auth, sid, pending=1)
    assert r.status_code == 409 and r.json()["error"]["code"] == "SETTLEMENT_PENDING_EVENTS"
    assert settle(client, auth, sid, seq=3).status_code == 409                      # phone ahead of the server: unsynced events
    ok = settle(client, auth, sid).json()
    assert ok["settlement"] == "ACKNOWLEDGED" and ok["settled_by"] == "disp.kiran"
    assert settle(client, auth, sid).json()["settlement"] == "ACKNOWLEDGED"          # idempotent
    assert close(client, auth, shp["id"]).json()["status"] == "CLOSED"


def test_empty_queue_refresh_settles_older_authorizations_never_the_new_one(client, auth, db):
    *_, shp = loaded_online(client, auth, db)
    s1 = snap(client, auth, shp["id"]).json()["snapshot_id"]
    r2 = client.get(f"{API}/offline/snapshot", params={"shipment_id": shp["id"], "device_id": "dev-a1", "local_seq": 0, "local_head": GEN, "pending": 0},
                    headers=auth("disp.kiran")).json()
    assert r2["superseded"] == [s1]
    assert db.get(m.OfflineSnapshot, s1).settlement == "SUPERSEDED"
    r = close(client, auth, shp["id"])
    assert blockers(r) == ["SNAPSHOT_UNSETTLED"] and r.json()["error"]["details"]["blockers"][0]["snapshot_id"] == r2["snapshot_id"]
    # A refresh that admits unsynced events settles nothing.
    r3 = client.get(f"{API}/offline/snapshot", params={"shipment_id": shp["id"], "device_id": "dev-a1", "local_seq": 0, "local_head": GEN, "pending": 2},
                    headers=auth("disp.kiran")).json()
    assert r3["superseded"] == [] and db.get(m.OfflineSnapshot, r2["snapshot_id"]).settled_at is None


def test_expiry_does_not_settle(client, auth, db):
    *_, shp = loaded_online(client, auth, db)
    sid = snap(client, auth, shp["id"]).json()["snapshot_id"]
    db.get(m.OfflineSnapshot, sid).expires_at = datetime.now(UTC) - timedelta(hours=1); db.commit()
    r = close(client, auth, shp["id"])
    assert blockers(r) == ["SNAPSHOT_UNSETTLED"] and "expired" in r.json()["error"]["details"]["blockers"][0]["message"]


def test_capture_after_settlement_is_a_conflict_and_blocks_departure(client, auth, db):
    _, _, order, a, b, shp = setup(client, auth, db)
    scan(client, auth, shp["id"], b["token"], "on-2")
    sid = snap(client, auth, shp["id"]).json()["snapshot_id"]
    assert settle(client, auth, sid).status_code == 200
    r = sync(client, auth, "dev-a1", sid, events("dev-a1", [(a["token"], shp["id"], "PROVISIONAL_ACCEPT")])).json()["results"][0]
    assert r["outcome"] == "CONFLICT" and "SNAPSHOT_SETTLED" in r["note"] and r["segregation_task_id"]
    assert _count(db, m.Load, m.Load.package_id == a["id"]) == 0
    assert "PHYSICAL_TASK_OPEN" in blockers(close(client, auth, shp["id"]))


def test_lost_device_recovery_needs_physical_reconciliation_before_release(client, auth, db):
    *_, a, b, shp = loaded_online(client, auth, db)
    sid = snap(client, auth, shp["id"]).json()["snapshot_id"]
    assert recover(client, auth, sid, who="disp.kiran").status_code == 403
    assert recover(client, auth, sid, reason="lost").status_code == 422             # a real reason is required
    req = recover(client, auth, sid).json()
    assert req["recovery_requested_at"] and req["settlement"] is None and req["recovery_task_id"]
    assert db.scalars(select(m.AuditEvent).where(m.AuditEvent.action == "OFFLINE_DEVICE_RECOVERY_REQUESTED")).first().reason.startswith("Phone dropped")
    assert db.scalars(select(m.Alert).where(m.Alert.type == "OFFLINE_DEVICE_RECOVERED")).first() is not None
    assert recover(client, auth, sid).json()["error"]["code"] == "RECOVERY_IN_PROGRESS"
    assert settle(client, auth, sid).json()["error"]["code"] == "SNAPSHOT_RECOVERED"  # the phone cannot quietly undo it
    # Requesting recovery does not release the truck: the reconciliation is still owed.
    assert set(blockers(close(client, auth, shp["id"]))) == {"SNAPSHOT_UNSETTLED", "PHYSICAL_TASK_OPEN"}
    # The phone turns up and syncs what it captured: authority ended at the recovery request.
    r = sync(client, auth, "dev-a1", sid, events("dev-a1", [(a["token"], shp["id"], "PROVISIONAL_ACCEPT")])).json()["results"][0]
    assert r["outcome"] == "CONFLICT" and "SNAPSHOT_RECOVERED" in r["note"]
    tid = req["recovery_task_id"]
    assert client.post(f"{API}/segregation-tasks/{tid}/complete", json={"reason": "counted"}, headers=auth("disp.kiran")).status_code == 403
    done = client.post(f"{API}/segregation-tasks/{tid}/complete", json={"reason": "Counted Dock 3 truck and lane: 2 crates, both on the manifest"},
                       headers=auth("qe.ananya")).json()
    assert done["status"] == "COMPLETED"
    sn = db.get(m.OfflineSnapshot, sid); db.refresh(sn)
    assert sn.settlement == "RECOVERED" and sn.recovery_evidence == [{"task_id": tid}] and "Counted Dock 3" in sn.settlement_reason
    # The late capture's own task (crate is also legitimately loaded) is verified by a QE; then the truck may go.
    assert client.post(f"{API}/segregation-tasks/{r['segregation_task_id']}/complete", json={"reason": "Verified: one crate, loaded online"},
                       headers=auth("qe.ananya")).status_code == 200
    assert close(client, auth, shp["id"]).json()["status"] == "CLOSED"


def upload(client, auth, who="qe.ananya"):
    r = client.post(f"{API}/evidence", files={"file": ("recount.pdf", b"%PDF-1.4\n%%EOF\n", "application/pdf")}, data={"purpose": "REVIEW_RECORD"},
                    headers=auth(who))
    assert r.status_code in (200, 201), r.text
    return r.json()["sha256"]


def test_recovery_with_linked_reconciliation_evidence_settles_at_once(client, auth, db):
    *_, shp = loaded_online(client, auth, db)
    sid = snap(client, auth, shp["id"]).json()["snapshot_id"]
    assert recover(client, auth, sid, evidence=["0" * 64]).status_code == 422          # evidence must exist
    sha = upload(client, auth)
    rec = recover(client, auth, sid, evidence=[sha]).json()
    assert rec["settlement"] == "RECOVERED" and rec["recovery_evidence"][0]["sha256"] == sha
    assert db.get(m.SegregationTask, rec["recovery_task_id"]).status == "COMPLETED"
    assert close(client, auth, shp["id"]).json()["status"] == "CLOSED"


def test_settlement_binds_sequence_and_hash(client, auth, db):
    _, _, _, a, _, shp = setup(client, auth, db)
    sid = snap(client, auth, shp["id"]).json()["snapshot_id"]
    evs = events("dev-a1", [(a["token"], shp["id"], "PROVISIONAL_ACCEPT")])
    sync(client, auth, "dev-a1", sid, evs)
    wrong = settle(client, auth, sid, seq=1, head="f" * 64)                            # same length, different history
    assert wrong.status_code == 409 and wrong.json()["error"]["details"]["server_head"] == evs[0]["hash"]
    ok = settle(client, auth, sid, seq=1, head=evs[0]["hash"]).json()
    assert ok["settled_seq"] == 1 and ok["settled_head"] == evs[0]["hash"]


def test_authority_is_judged_at_receipt_not_by_claimed_capture_time(client, auth, db):
    _, _, _, a, b, shp = setup(client, auth, db)
    s1 = snap(client, auth, shp["id"]).json()["snapshot_id"]
    assert settle(client, auth, s1).status_code == 200
    backdated = events("dev-a1", [(a["token"], shp["id"], "PROVISIONAL_ACCEPT")])     # "captured" before settlement, received after
    r = sync(client, auth, "dev-a1", s1, backdated).json()["results"][0]
    assert r["outcome"] == "CONFLICT" and "SNAPSHOT_SETTLED" in r["note"]
    s2 = snap(client, auth, shp["id"]).json()["snapshot_id"]
    late = events("dev-a1", [(b["token"], shp["id"], "PROVISIONAL_ACCEPT")], start_seq=2, head=backdated[0]["hash"])
    db.get(m.OfflineSnapshot, s2).expires_at = datetime.now(UTC) - timedelta(seconds=1); db.commit()   # it expired before receipt
    r = sync(client, auth, "dev-a1", s2, late).json()["results"][0]
    assert r["outcome"] == "CONFLICT" and "SNAPSHOT_EXPIRED_AT_RECEIPT" in r["note"]
    assert _count(db, m.Load) == 0


def test_snapshot_issuance_and_close_are_serialized(client, auth, db):
    *_, shp = loaded_online(client, auth, db)
    h = auth("disp.kiran")

    def issue(_):
        return client.get(f"{API}/offline/snapshot", params={"shipment_id": shp["id"], "device_id": "dev-a1"}, headers=h).status_code

    def do_close(_):
        return client.post(f"{API}/shipments/{shp['id']}/close", headers=h).status_code
    with cf.ThreadPoolExecutor(2) as ex:
        a, b = ex.submit(issue, 0), ex.submit(do_close, 0)
        issued, closed = a.result(), b.result()
    # Either the close won (and issuance was refused for a closed manifest) or the snapshot won (and close was refused).
    assert (issued, closed) in ((409, 200), (200, 409))
    final = db.get(m.Shipment, shp["id"]); db.refresh(final)
    open_snaps = _count(db, m.OfflineSnapshot, m.OfflineSnapshot.shipment_id == shp["id"], m.OfflineSnapshot.settled_at.is_(None))
    assert (final.status == "CLOSED") == (open_snaps == 0)


# ── 2. replay authorization and provenance ─────────────────────────────────────────────────────────────────────
def test_signed_allowlist_is_enforced_even_when_the_live_gate_would_accept(client, auth, db):
    _, units, order, a, b, shp = setup(client, auth, db, qty=10)
    sid = snap(client, auth, shp["id"]).json()["snapshot_id"]
    late = sealed(client, auth, order["id"], [u["id"] for u in units[8:10]], seed=20)   # created after the snapshot
    r = sync(client, auth, "dev-a1", sid, events("dev-a1", [(late["token"], shp["id"], "PROVISIONAL_ACCEPT")])).json()["results"][0]
    assert r["outcome"] == "CONFLICT" and "NOT_IN_SNAPSHOT" in r["note"] and r["scan"]["decision"] == "BLOCKED"
    assert _count(db, m.Load, m.Load.package_id == late["id"]) == 0


def test_allowlist_reasons():
    class P:
        id, label = "pkg_1", "PKG-0001"
    assert _allowlist_reasons({"allowed_packages": ["pkg_1"], "held": []}, P) == []
    assert _allowlist_reasons({"allowed_packages": ["pkg_2"], "held": []}, P)[0]["code"] == "NOT_IN_SNAPSHOT"
    assert _allowlist_reasons({"allowed_packages": ["pkg_1"], "held": ["pkg_1"]}, P)[0]["code"] == "HELD_AT_SNAPSHOT"
    assert _allowlist_reasons({"allowed_packages": [], "held": []}, None) == []


def test_an_accepted_scan_never_stands_in_for_a_distinct_blocked_or_provisional_event(client, auth, db):
    _, _, _, a, b, shp = setup(client, auth, db)
    sid = snap(client, auth, shp["id"]).json()["snapshot_id"]
    other = scan(client, auth, shp["id"], a["token"], "dev-a1:000001").json()       # another device used this id, same content
    other2 = scan(client, auth, shp["id"], b["token"], "dev-a1:000002").json()
    evs = events("dev-a1", [(a["token"], shp["id"], "LOCAL_BLOCK"), (b["token"], shp["id"], "PROVISIONAL_ACCEPT")])
    r = sync(client, auth, "dev-a1", sid, evs).json()["results"]
    assert [x["outcome"] for x in r] == ["CONFLICT", "CONFLICT"] and r[0]["scan"] is None and r[1]["scan"] is None
    assert r[0]["segregation_task_id"] is None and r[1]["segregation_task_id"]      # only the provisional one may be on the truck
    assert {db.get(m.DeviceEvent, ("dev-a1", n)).scan_id for n in (1, 2)} == {None}
    assert db.get(m.Scan, other["scan_id"]).decision == db.get(m.Scan, other2["scan_id"]).decision == "ACCEPTED"


def test_replay_must_cite_the_same_snapshot_as_first_time(client, auth, db):
    _, _, _, a, _, shp = setup(client, auth, db)
    s1 = snap(client, auth, shp["id"]).json()["snapshot_id"]
    s2 = snap(client, auth, shp["id"]).json()["snapshot_id"]
    evs = events("dev-a1", [(a["token"], shp["id"], "PROVISIONAL_ACCEPT")])
    assert sync(client, auth, "dev-a1", s1, evs).json()["results"][0]["outcome"] == "ACCEPTED"
    changed = sync(client, auth, "dev-a1", s2, evs)
    assert changed.status_code == 409 and changed.json()["error"]["details"]["field"] == "snapshot_id"
    again = sync(client, auth, "dev-a1", s1, evs).json()["results"][0]
    assert again["outcome"] == "DUPLICATE" and again["original_outcome"] == "ACCEPTED"
    assert db.get(m.DeviceEvent, ("dev-a1", 1)).claimed_snapshot_id == s1


def test_capture_times_must_carry_a_timezone(client, auth, db):
    _, _, _, a, _, shp = setup(client, auth, db)
    sid = snap(client, auth, shp["id"]).json()["snapshot_id"]
    evs = events("dev-a1", [(a["token"], shp["id"], "PROVISIONAL_ACCEPT")])
    evs[0]["scanned_at"] = "2026-11-26T10:00:00"
    from app.features.passport_crypto.service import chain_hash
    evs[0]["hash"] = chain_hash(GEN, evs[0])
    r = sync(client, auth, "dev-a1", sid, evs)
    assert r.status_code == 422 and "timezone" in r.text
    assert _count(db, m.DeviceEvent) == 0


def test_lost_online_response_recovers_the_original_decision_and_package(client, auth, db):
    _, _, _, a, _, shp = setup(client, auth, db)
    sid = snap(client, auth, shp["id"]).json()["snapshot_id"]
    online = client.post(f"{API}/shipments/{shp['id']}/scans", json={"token": a["token"], "client_event_id": "dev-a1:000001", "device_id": "dev-a1"},
                         headers=auth("disp.kiran")).json()                    # committed, but the phone never saw the response
    r = sync(client, auth, "dev-a1", sid, events("dev-a1", [(a["token"], shp["id"], "ONLINE")])).json()["results"][0]
    assert r["outcome"] == "DUPLICATE" and r["original_outcome"] == "ACCEPTED" and r["note"] == "Online decision recovered: ACCEPTED"
    assert r["scan"]["scan_id"] == online["scan_id"] and r["scan"]["package_label"] == a["label"]


# ── 3. shipment-safe segregation ───────────────────────────────────────────────────────────────────────────────
def _conflict_across_shipments(client, auth, db):
    """Package a is legitimately loaded on shipment 2; a phone staged it offline for shipment 1 (refused → task on shipment 1)."""
    _, _, order, a, b, shp1 = setup(client, auth, db)
    shp2 = client.post(f"{API}/shipments", json={"order_id": order["id"], "vehicle_no": "MH-12-ZZ-0002", "dock": "Dock 4"}, headers=auth("disp.kiran")).json()
    sid = snap(client, auth, shp1["id"]).json()["snapshot_id"]
    assert scan(client, auth, shp2["id"], a["token"], "legit-on-2").json()["decision"] == "ACCEPTED"
    r = sync(client, auth, "dev-a1", sid, events("dev-a1", [(a["token"], shp1["id"], "PROVISIONAL_ACCEPT")])).json()["results"][0]
    assert r["outcome"] == "CONFLICT" and "ALREADY_LOADED" in r["note"]
    return a, b, shp1, shp2, r["segregation_task_id"]


def _active_load(db, pkg_id):
    db.expire_all()
    return db.scalars(select(m.Load).where(m.Load.package_id == pkg_id, m.Load.removed_at.is_(None))).first()


def test_task_for_one_shipment_never_removes_another_shipments_load(client, auth, db):
    a, _, shp1, shp2, tid = _conflict_across_shipments(client, auth, db)
    r = client.post(f"{API}/segregation-tasks/{tid}/complete", json={"reason": "pulled it off"}, headers=auth("disp.kiran"))
    assert r.status_code == 409 and r.json()["error"]["code"] == "LOCATION_CONFLICT_REVIEW"
    assert _active_load(db, a["id"]).shipment_id == shp2["id"]
    ok = client.post(f"{API}/segregation-tasks/{tid}/complete", json={"reason": "Verified at Dock 4: crate is on SHP-2, not on the Dock 3 truck"},
                     headers=auth("qe.ananya")).json()
    assert ok["status"] == "COMPLETED" and ok["location_conflict"] == {"recorded_on": shp2["code"], "load_left_unchanged": True}
    assert _active_load(db, a["id"]).shipment_id == shp2["id"]                       # the legitimate load is untouched
    assert db.scalars(select(m.AuditEvent).where(m.AuditEvent.action == "SEGREGATION_LOCATION_CONFLICT_RESOLVED")).first() is not None


def test_completion_order_removal_on_the_owning_shipment_first(client, auth, db):
    a, _, shp1, shp2, tid = _conflict_across_shipments(client, auth, db)
    rid = segregation.create(db, type="REMOVAL_REQUIRED", dedupe_key="recall-like", package_id=a["id"], shipment_id=shp2["id"], reason="held after load")
    db.commit()
    assert client.post(f"{API}/segregation-tasks/{rid}/complete", json={"reason": "unloaded at Dock 4"}, headers=auth("disp.kiran")).status_code == 200
    assert _active_load(db, a["id"]) is None
    r = client.post(f"{API}/segregation-tasks/{tid}/complete", json={"reason": "crate in quarantine cage"}, headers=auth("disp.kiran")).json()
    assert r["status"] == "COMPLETED" and "location_conflict" not in r                # no load left anywhere: plain disposition


def test_completion_order_conflict_task_first(client, auth, db):
    a, _, shp1, shp2, tid = _conflict_across_shipments(client, auth, db)
    rid = segregation.create(db, type="REMOVAL_REQUIRED", dedupe_key="recall-like", package_id=a["id"], shipment_id=shp2["id"], reason="held after load")
    db.commit()
    assert client.post(f"{API}/segregation-tasks/{tid}/complete", json={"reason": "Verified: crate is on SHP-2"}, headers=auth("qe.ananya")).status_code == 200
    assert _active_load(db, a["id"]).shipment_id == shp2["id"]
    assert client.post(f"{API}/segregation-tasks/{rid}/complete", json={"reason": "unloaded at Dock 4"}, headers=auth("disp.kiran")).status_code == 200
    assert _active_load(db, a["id"]) is None


def test_concurrent_completions_and_scans_stay_consistent(client, auth, db):
    a, b, shp1, shp2, tid = _conflict_across_shipments(client, auth, db)
    rid = segregation.create(db, type="REMOVAL_REQUIRED", dedupe_key="recall-like", package_id=a["id"], shipment_id=shp2["id"], reason="held after load")
    db.commit()
    jobs = [("POST", f"{API}/segregation-tasks/{tid}/complete", {"reason": "Verified location"}, "qe.ananya"),
            ("POST", f"{API}/segregation-tasks/{rid}/complete", {"reason": "unloaded at Dock 4"}, "disp.kiran"),
            ("POST", f"{API}/shipments/{shp1['id']}/scans", {"token": b["token"], "client_event_id": "race-b"}, "disp.kiran")]

    def go(job):
        _, url, body, who = job
        return client.post(url, json=body, headers=auth(who)).status_code
    with cf.ThreadPoolExecutor(3) as ex:
        codes = list(ex.map(go, jobs))
    assert codes == [200, 200, 200]
    assert _active_load(db, a["id"]) is None and _active_load(db, b["id"]).shipment_id == shp1["id"]
    assert _count(db, m.SegregationTask, m.SegregationTask.status == "OPEN") == 0


def test_offline_duplicate_on_the_same_truck_never_unloads_the_legitimate_load(client, auth, db):
    _, _, _, a, _, shp = setup(client, auth, db)
    sid = snap(client, auth, shp["id"]).json()["snapshot_id"]
    assert scan(client, auth, shp["id"], a["token"], "legit").json()["decision"] == "ACCEPTED"     # control room loaded it
    r = sync(client, auth, "dev-a1", sid, events("dev-a1", [(a["token"], shp["id"], "PROVISIONAL_ACCEPT")])).json()["results"][0]
    assert r["outcome"] == "CONFLICT" and "ALREADY_LOADED" in r["note"]
    tid = r["segregation_task_id"]
    deny = client.post(f"{API}/segregation-tasks/{tid}/complete", json={"reason": "unloaded"}, headers=auth("disp.kiran"))
    assert deny.status_code == 409 and deny.json()["error"]["code"] == "LOCATION_CONFLICT_REVIEW"
    ok = client.post(f"{API}/segregation-tasks/{tid}/complete", json={"reason": "One crate, already on this truck from the control-room scan"},
                     headers=auth("qe.ananya")).json()
    assert ok["location_conflict"]["recorded_on"] == shp["code"] and _active_load(db, a["id"]).shipment_id == shp["id"]


# ── replay details (review of #33/#34) ─────────────────────────────────────────────────────────────────────────
def test_a_known_null_claim_is_not_mistaken_for_missing_legacy_metadata(client, auth, db):
    _, _, _, a, _, shp = setup(client, auth, db)
    sid = snap(client, auth, shp["id"]).json()["snapshot_id"]
    evs = events("dev-a1", [(a["token"], shp["id"], "ONLINE")])
    assert sync(client, auth, "dev-a1", None, evs).json()["results"][0]["outcome"] == "BLOCKED"   # synced with NO snapshot
    assert db.get(m.DeviceEvent, ("dev-a1", 1)).claim_recorded is True
    swapped = sync(client, auth, "dev-a1", sid, evs)                                   # now claims one
    assert swapped.status_code == 409 and swapped.json()["error"]["details"]["field"] == "snapshot_id"
    # A pre-0006 row: the claim was never recorded, so it cannot be compared (and is not invented).
    db.execute(update(m.DeviceEvent).where(m.DeviceEvent.device_id == "dev-a1").values(claim_recorded=False)); db.commit()
    assert sync(client, auth, "dev-a1", sid, evs).json()["results"][0]["outcome"] == "DUPLICATE"


def test_replay_compares_the_submitted_previous_hash(client, auth, db):
    _, _, _, a, _, shp = setup(client, auth, db)
    sid = snap(client, auth, shp["id"]).json()["snapshot_id"]
    evs = events("dev-a1", [(a["token"], shp["id"], "PROVISIONAL_ACCEPT")])
    sync(client, auth, "dev-a1", sid, evs)
    forged = [dict(evs[0], prev_hash="a" * 64)]                                        # hash field kept, link changed
    r = sync(client, auth, "dev-a1", sid, forged)
    assert r.status_code == 409 and r.json()["error"]["code"] == "EVENT_CONTENT_CHANGED"


# ── commit order: a shipment-blocking task vs departure ────────────────────────────────────────────────────────
def _waiting_on_locks(n, timeout=10.0):
    """pg_stat_activity is frozen for the length of a transaction, so poll on a fresh session each time."""
    end = time.time() + timeout
    while time.time() < end:
        with session_factory()() as s:
            got = s.execute(text("SELECT count(*) FROM pg_stat_activity WHERE wait_event_type = 'Lock' AND datname = current_database()")).scalar()
        if got >= n:
            return
        time.sleep(0.05)
    raise AssertionError(f"expected {n} transactions waiting on row locks")


def _closed_with_a_settled_phone(client, auth, db):
    *_, a, b, shp = loaded_online(client, auth, db)
    sid = snap(client, auth, shp["id"]).json()["snapshot_id"]
    assert settle(client, auth, sid).status_code == 200
    assert close(client, auth, shp["id"]).json()["status"] == "CLOSED"
    return a, shp, sid


def _race(client, auth, db, first: str):
    """Hold the shipment row, queue the two transactions in a chosen order behind it, release, and let them commit."""
    a, shp, sid = _closed_with_a_settled_phone(client, auth, db)
    late = events("dev-a1", [(a["token"], shp["id"], "PROVISIONAL_ACCEPT")])           # a phone event that arrives late
    jobs = {"sync": lambda: sync(client, auth, "dev-a1", sid, late),
            "depart": lambda: client.post(f"{API}/shipments/{shp['id']}/events", json={"type": "DEPARTED", "location": "Gate"}, headers=auth("disp.kiran"))}
    second = "depart" if first == "sync" else "sync"
    holder = session_factory()()
    holder.execute(text("SELECT id FROM shipments WHERE id = :i FOR UPDATE"), {"i": shp["id"]})
    with cf.ThreadPoolExecutor(2) as ex:
        try:
            f1 = ex.submit(jobs[first]); _waiting_on_locks(1)
            f2 = ex.submit(jobs[second]); _waiting_on_locks(2)
        finally:
            holder.commit(); holder.close()                                          # always release, or the threads never finish
        out = {first: f1.result(timeout=60), second: f2.result(timeout=60)}
    db.expire_all()
    return out, db.get(m.Shipment, shp["id"])


def test_commit_order_task_first_blocks_departure(client, auth, db):
    out, ship = _race(client, auth, db, first="sync")
    assert out["sync"].json()["results"][0]["outcome"] == "CONFLICT"
    assert out["depart"].status_code == 409 and out["depart"].json()["error"]["code"] == "DEPARTURE_NOT_RECONCILED"
    assert ship.status == "CLOSED"


def test_commit_order_departure_first_then_the_late_event_is_recorded_against_the_departed_truck(client, auth, db):
    out, ship = _race(client, auth, db, first="depart")
    assert out["depart"].status_code == 200 and ship.status == "IN_TRANSIT"
    r = out["sync"].json()["results"][0]
    assert r["outcome"] == "CONFLICT" and r["segregation_task_id"]                    # nothing lost: a task and an alert, no load
    assert db.get(m.SegregationTask, r["segregation_task_id"]).shipment_id == ship.id
