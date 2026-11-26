"""Offline snapshot + idempotent, chain-verified replay through the live dispatch gate. Real PostgreSQL."""
import time
from datetime import UTC, datetime

from sqlalchemy import func, select

from app.db import models as m
from app.features.inspection_passports.passports import trusted_keys
from app.features.passport_crypto.service import chain_hash, verify_snapshot
from tests.test_dispatch import setup

API = "/api/v1"
GEN = "0" * 64


def events(device, specs, start_seq=1, head=GEN):
    """specs: [(token, shipment_id, local_decision)] → hash-chained events exactly as frontend/src/lib/chain.ts builds them."""
    out, prev = [], head
    for i, (tok, shp, ld) in enumerate(specs):
        seq = start_seq + i
        e = {"client_event_id": f"{device}:{seq:06d}", "seq": seq, "token": tok, "shipment_id": shp,
             "scanned_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"), "local_decision": ld}
        e["prev_hash"], e["hash"] = prev, chain_hash(prev, e)
        prev = e["hash"]
        out.append(e)
    return out


def snap(client, auth, shp, device="dev-a1", who="disp.kiran"):
    return client.get(f"{API}/offline/snapshot", params={"shipment_id": shp, "device_id": device}, headers=auth(who))


def sync(client, auth, device, sid, evs, who="disp.kiran"):
    return client.post(f"{API}/sync/scans", json={"device_id": device, "snapshot_id": sid, "events": evs}, headers=auth(who))


def test_snapshot_is_signed_scoped_and_role_gated(client, auth, db):
    _, _, order, a, b, shp = setup(client, auth, db)
    assert snap(client, auth, shp["id"], who="op.ravi").status_code == 403
    s = snap(client, auth, shp["id"]).json()
    me = client.get(f"{API}/auth/me", headers=auth("disp.kiran")).json()
    v = verify_snapshot(s["token"], trusted_keys(db), int(time.time()), expected_shipment=shp["id"], expected_order=order["id"],
                        expected_device_id="dev-a1", expected_operator_id=me["id"], expected_policy_version="demo-1")
    assert v.payload["expected_units"] == 8 and sorted(v.payload["allowed_packages"]) == sorted([a["id"], b["id"]]) and v.payload["held"] == []
    from tests.test_dispatch import scan
    scan(client, auth, shp["id"], a["token"], "online-1")
    s2 = verify_snapshot(snap(client, auth, shp["id"]).json()["token"], trusted_keys(db), int(time.time()), expected_shipment=shp["id"], expected_order=order["id"],
                         expected_device_id="dev-a1", expected_operator_id=me["id"], expected_policy_version="demo-1")
    assert s2.payload["expected_units"] == 4 and s2.payload["allowed_packages"] == [b["id"]]   # remaining capacity after committed loads


def test_sync_accepts_provisional_load_and_replay_is_idempotent(client, auth, db):
    _, _, _, a, b, shp = setup(client, auth, db)
    sid = snap(client, auth, shp["id"]).json()["snapshot_id"]
    evs = events("dev-a1", [(a["token"], shp["id"], "PROVISIONAL_ACCEPT"), (b["token"], shp["id"], "PROVISIONAL_ACCEPT")])
    r = sync(client, auth, "dev-a1", sid, evs).json()
    assert [x["outcome"] for x in r["results"]] == ["ACCEPTED", "ACCEPTED"] and r["chain"] == {"ok": True, "server_head": evs[-1]["hash"], "server_seq": 2}
    assert r["results"][0]["scan"]["offline"] is True
    again = sync(client, auth, "dev-a1", sid, evs).json()
    assert [x["outcome"] for x in again["results"]] == ["DUPLICATE", "DUPLICATE"] and [x["original_outcome"] for x in again["results"]] == ["ACCEPTED", "ACCEPTED"]
    assert db.scalar(select(func.count()).select_from(m.Load)) == 2
    tampered = [dict(evs[0], token=b["token"])] + evs[1:]
    bad = sync(client, auth, "dev-a1", sid, tampered)
    assert bad.status_code == 409 and bad.json()["error"]["code"] == "EVENT_CONTENT_CHANGED"


def test_broken_chain_and_gaps_advance_nothing(client, auth, db):
    _, _, _, a, b, shp = setup(client, auth, db)
    sid = snap(client, auth, shp["id"]).json()["snapshot_id"]
    evs = events("dev-a1", [(a["token"], shp["id"], "PROVISIONAL_ACCEPT"), (b["token"], shp["id"], "PROVISIONAL_ACCEPT")])
    evs[1]["token"] = a["token"]                                        # edited after capture
    r = sync(client, auth, "dev-a1", sid, evs).json()
    assert r["results"] == [] and r["chain"]["ok"] is False and r["chain"]["broken_at"] == 2 and r["chain"]["server_seq"] == 0
    gap = events("dev-a1", [(a["token"], shp["id"], "PROVISIONAL_ACCEPT")], start_seq=2)
    assert sync(client, auth, "dev-a1", sid, gap).json()["chain"] == {"ok": False, "broken_at": 2, "server_head": GEN, "server_seq": 0}
    assert db.scalar(select(func.count()).select_from(m.Load)) == 0 and db.scalar(select(func.count()).select_from(m.DeviceEvent)) == 0


def test_hold_after_snapshot_becomes_conflict_with_one_segregation_task(client, auth, db):
    c, _, _, a, _, shp = setup(client, auth, db)
    sid = snap(client, auth, shp["id"]).json()["snapshot_id"]
    client.post(f"{API}/four-m-events", json={"category": "MACHINE", "planned": False, "line_id": c["line"]["id"], "description": "insert", "reason": "chip"},
                headers=auth("qe.ananya"))
    evs = events("dev-a1", [(a["token"], shp["id"], "PROVISIONAL_ACCEPT")])
    r = sync(client, auth, "dev-a1", sid, evs).json()["results"][0]
    assert r["outcome"] == "CONFLICT" and "CONTAINMENT_ACTIVE" in r["note"] and r["segregation_task_id"]
    sync(client, auth, "dev-a1", sid, evs)                               # replay → no second task
    assert db.scalar(select(func.count()).select_from(m.SegregationTask).where(m.SegregationTask.type == "SEGREGATION_REQUIRED")) == 1
    assert db.scalars(select(m.Alert).where(m.Alert.type == "OFFLINE_CONFLICT")).first().audible_siren
    assert db.scalar(select(func.count()).select_from(m.Load)) == 0


def test_two_devices_stage_the_same_package(client, auth, db):
    _, _, _, a, _, shp = setup(client, auth, db)
    s1 = snap(client, auth, shp["id"], "dev-a1").json()["snapshot_id"]
    s2 = snap(client, auth, shp["id"], "dev-b2").json()["snapshot_id"]
    r1 = sync(client, auth, "dev-a1", s1, events("dev-a1", [(a["token"], shp["id"], "PROVISIONAL_ACCEPT")])).json()["results"][0]
    r2 = sync(client, auth, "dev-b2", s2, events("dev-b2", [(a["token"], shp["id"], "PROVISIONAL_ACCEPT")])).json()["results"][0]
    assert r1["outcome"] == "ACCEPTED" and r2["outcome"] == "CONFLICT" and "ALREADY_LOADED" in r2["note"]
    assert db.scalar(select(func.count()).select_from(m.Load)) == 1


def test_local_block_and_lost_online_are_never_loaded(client, auth, db):
    _, _, _, a, b, shp = setup(client, auth, db)
    sid = snap(client, auth, shp["id"]).json()["snapshot_id"]
    online = client.post(f"{API}/shipments/{shp['id']}/scans", json={"token": a["token"], "client_event_id": "dev-a1:000001", "device_id": "dev-a1"},
                         headers=auth("disp.kiran")).json()                          # online decision reached the server (from this phone)
    evs = events("dev-a1", [(a["token"], shp["id"], "ONLINE"), (b["token"], shp["id"], "ONLINE"), (b["token"], shp["id"], "LOCAL_BLOCK")])
    r = sync(client, auth, "dev-a1", sid, evs).json()["results"]
    assert [x["outcome"] for x in r] == ["DUPLICATE", "BLOCKED", "BLOCKED"] and r[0]["scan"]["scan_id"] == online["scan_id"]
    assert db.scalar(select(func.count()).select_from(m.Load)) == 1


def test_device_bound_to_operator_and_close_blocks_unsynced(client, auth, db):
    _, _, _, a, b, shp = setup(client, auth, db)
    snap(client, auth, shp["id"], "dev-a1")
    db.add(m.UserRole(user_id="usr_farhan", role="dispatcher")); db.commit()
    other = snap(client, auth, shp["id"], "dev-a1", who="qm.farhan")
    assert other.status_code == 409 and other.json()["error"]["code"] == "DEVICE_OPERATOR_MISMATCH"
    from tests.test_dispatch import scan
    scan(client, auth, shp["id"], a["token"], "c1"); scan(client, auth, shp["id"], b["token"], "c2")
    r = client.post(f"{API}/shipments/{shp['id']}/close", json={"unsynced_offline_events": 2}, headers=auth("disp.kiran"))
    codes = [b["code"] for b in r.json()["error"]["details"]["blockers"]]
    assert r.status_code == 409 and codes == ["PROVISIONAL_PENDING", "SNAPSHOT_UNSETTLED"]   # dev-a1 still holds its authorization
    settle_all(client, auth, shp["id"], "dev-a1")
    assert client.post(f"{API}/shipments/{shp['id']}/close", headers=auth("disp.kiran")).json()["status"] == "CLOSED"


# ── provenance, lost responses and close enforcement (A7 handoff) ──────────────────────────────────────────────
def _count(db, model, *where):
    return db.scalar(select(func.count()).select_from(model).where(*where))


def test_provisional_load_without_a_snapshot_is_a_conflict_not_a_load(client, auth, db):
    _, _, _, a, _, shp = setup(client, auth, db)
    snap(client, auth, shp["id"])                                       # registers the device; the event names no snapshot
    r = sync(client, auth, "dev-a1", None, events("dev-a1", [(a["token"], shp["id"], "PROVISIONAL_ACCEPT")])).json()["results"][0]
    assert r["outcome"] == "CONFLICT" and "SNAPSHOT_REQUIRED" in r["note"] and r["segregation_task_id"]
    assert _count(db, m.Load) == 0


def test_snapshot_must_belong_to_this_device_operator_and_shipment(client, auth, db):
    _, _, _, a, b, shp = setup(client, auth, db)
    mine = snap(client, auth, shp["id"], "dev-a1").json()["snapshot_id"]
    theirs = snap(client, auth, shp["id"], "dev-b2").json()["snapshot_id"]
    evs = events("dev-a1", [(a["token"], shp["id"], "PROVISIONAL_ACCEPT"), (b["token"], shp["id"], "PROVISIONAL_ACCEPT")])
    evs[0]["snapshot_id"] = theirs                                      # per-event claim overrides the batch default
    r = sync(client, auth, "dev-a1", mine, evs).json()["results"]
    assert [x["outcome"] for x in r] == ["CONFLICT", "ACCEPTED"] and "SNAPSHOT_SCOPE_MISMATCH" in r[0]["note"]
    stored = db.get(m.DeviceEvent, ("dev-a1", 1))
    assert stored.snapshot_id is None and db.get(m.DeviceEvent, ("dev-a1", 2)).snapshot_id == mine


def test_capture_outside_the_snapshot_window_is_refused(client, auth, db):
    _, _, _, a, b, shp = setup(client, auth, db)
    sid = snap(client, auth, shp["id"]).json()["snapshot_id"]
    evs = events("dev-a1", [(a["token"], shp["id"], "PROVISIONAL_ACCEPT")])
    evs[0]["scanned_at"] = "2020-01-01T00:00:00Z"; evs[0]["hash"] = chain_hash(GEN, evs[0])      # before the snapshot existed
    r = sync(client, auth, "dev-a1", sid, evs).json()["results"][0]
    assert r["outcome"] == "CONFLICT" and "CAPTURE_BEFORE_SNAPSHOT" in r["note"]
    nxt = events("dev-a1", [(b["token"], shp["id"], "PROVISIONAL_ACCEPT")], start_seq=2, head=evs[0]["hash"])
    nxt[0]["scanned_at"] = "2099-01-01T00:00:00Z"; nxt[0]["hash"] = chain_hash(evs[0]["hash"], nxt[0])
    r = sync(client, auth, "dev-a1", sid, nxt).json()["results"][0]
    assert r["outcome"] == "CONFLICT" and "CAPTURE_TIME_INVALID" in r["note"]
    assert _count(db, m.Load) == 0


def test_online_event_is_linked_only_to_the_scan_it_produced(client, auth, db):
    _, _, _, a, b, shp = setup(client, auth, db)
    sid = snap(client, auth, shp["id"]).json()["snapshot_id"]
    from tests.test_dispatch import scan
    other = scan(client, auth, shp["id"], a["token"], "dev-a1:000001").json()   # same id, but recorded from another device
    r = sync(client, auth, "dev-a1", sid, events("dev-a1", [(b["token"], shp["id"], "ONLINE")])).json()["results"][0]
    assert r["outcome"] == "CONFLICT" and r["scan"] is None and "not linked" in r["note"]
    assert db.get(m.DeviceEvent, ("dev-a1", 1)).scan_id is None
    assert db.scalars(select(m.Alert).where(m.Alert.type == "OFFLINE_CONFLICT")).first().reason_codes == ["EVENT_PROVENANCE_MISMATCH"]
    assert _count(db, m.Load) == 1 and db.get(m.Scan, other["scan_id"]).decision == "ACCEPTED"   # the real scan is untouched


def test_lost_sync_response_still_returns_the_unload_instruction(client, auth, db):
    _, _, _, a, _, shp = setup(client, auth, db)
    s1 = snap(client, auth, shp["id"], "dev-a1").json()["snapshot_id"]
    s2 = snap(client, auth, shp["id"], "dev-b2").json()["snapshot_id"]
    sync(client, auth, "dev-a1", s1, events("dev-a1", [(a["token"], shp["id"], "PROVISIONAL_ACCEPT")]))
    evs = events("dev-b2", [(a["token"], shp["id"], "PROVISIONAL_ACCEPT")])
    first = sync(client, auth, "dev-b2", s2, evs).json()["results"][0]      # imagine this response never reached the phone
    again = sync(client, auth, "dev-b2", s2, evs).json()["results"][0]
    assert first["outcome"] == "CONFLICT" and again["outcome"] == "DUPLICATE" and again["original_outcome"] == "CONFLICT"
    assert again["note"] == first["note"] and "unload and segregate" in again["note"]
    assert again["segregation_task_id"] == first["segregation_task_id"] and again["segregation_task_status"] == "OPEN"


def settle_all(client, auth, shp_id, device, who="disp.kiran", seq=0, head=GEN):
    view = client.get(f"{API}/shipments/{shp_id}", headers=auth(who)).json()
    for o in view["offline_authorizations_open"]:
        if o["device_id"] == device:
            r = client.post(f"{API}/offline/snapshots/{o['snapshot_id']}/settle", json={"device_id": device, "local_seq": seq, "local_head": head, "pending": 0},
                            headers=auth(who))
            assert r.status_code == 200, r.text
