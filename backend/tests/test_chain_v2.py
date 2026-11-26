"""Device-chain v2 integration (A4): dual readers, version floor, legacy reconciliation, immutable associations."""
import concurrent.futures as cf
import time
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import IntegrityError

from app.db import models as m
from app.db.session import session_factory
from app.features.passport_crypto.service import chain_hash
from app.policy import policies
from tests.test_dispatch import setup
from tests.test_offline_scanner import GEN, snap, sync

API = "/api/v1"


def ev(device, seq, prev, token, shp, ld, *, version=1, snapshot_id=None):
    e = {"client_event_id": f"{device}:{seq:06d}", "seq": seq, "token": token, "shipment_id": shp,
         "scanned_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"), "local_decision": ld, "prev_hash": prev}
    if version == 2:
        e |= {"chain_version": 2, "snapshot_id": snapshot_id}
    elif snapshot_id:
        e["snapshot_id"] = snapshot_id
    e["hash"] = chain_hash(prev, e)
    return e


def set_chain_policy(db, writer, floor):
    s = policies.settings_of(policies.active(db)).model_copy(deep=True)
    s.offline.chain_writer_version, s.offline.chain_min_version = writer, floor
    policies.publish(db, policies.PolicySettings.model_validate(s.model_dump()), None)
    db.commit()


def test_one_device_chain_mixes_v1_history_with_v2_events(client, auth, db):
    _, _, _, a, b, shp = setup(client, auth, db)
    sid = snap(client, auth, shp["id"]).json()["snapshot_id"]
    e1 = ev("dev-a1", 1, GEN, a["token"], shp["id"], "PROVISIONAL_ACCEPT", snapshot_id=sid)               # legacy writer
    e2 = ev("dev-a1", 2, e1["hash"], b["token"], shp["id"], "PROVISIONAL_ACCEPT", version=2, snapshot_id=sid)
    r = sync(client, auth, "dev-a1", None, [e1, e2]).json()
    assert [x["outcome"] for x in r["results"]] == ["ACCEPTED", "ACCEPTED"] and [x["chain_version"] for x in r["results"]] == [1, 2]
    assert r["chain"]["chain_version_high"] == 2 and r["chain"]["supported"] == [1, 2]
    assert [db.get(m.DeviceEvent, ("dev-a1", n)).chain_version for n in (1, 2)] == [1, 2]
    again = sync(client, auth, "dev-a1", None, [e1, e2]).json()["results"]                                  # historical replay
    assert [x["original_outcome"] for x in again] == ["ACCEPTED", "ACCEPTED"]


def test_v2_binds_the_capture_snapshot_into_the_hash(client, auth, db):
    _, _, _, a, _, shp = setup(client, auth, db)
    s1 = snap(client, auth, shp["id"]).json()["snapshot_id"]
    s2 = snap(client, auth, shp["id"]).json()["snapshot_id"]
    e1 = ev("dev-a1", 1, GEN, a["token"], shp["id"], "PROVISIONAL_ACCEPT", version=2, snapshot_id=s1)
    assert sync(client, auth, "dev-a1", s2, [e1]).json()["results"][0]["outcome"] == "ACCEPTED"   # batch default ignored for v2
    assert db.get(m.DeviceEvent, ("dev-a1", 1)).claimed_snapshot_id == s1
    swapped = dict(e1, snapshot_id=s2)                                                                     # same hash, other snapshot
    assert sync(client, auth, "dev-a1", None, [swapped]).status_code == 409
    online = {"client_event_id": "dev-a1:000002", "seq": 2, "token": a["token"], "shipment_id": shp["id"], "local_decision": "ONLINE",
              "scanned_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"), "prev_hash": e1["hash"], "chain_version": 2, "snapshot_id": s1}
    online["hash"] = "c" * 64                                                                              # v2 refuses to hash it at all
    assert sync(client, auth, "dev-a1", None, [online]).json()["chain"]["ok"] is False                    # ONLINE must hash an explicit null


def test_version_floor_keeps_legacy_queues_reconcilable_without_authority(client, auth, db):
    _, _, _, a, b, shp = setup(client, auth, db)
    sid = snap(client, auth, shp["id"]).json()["snapshot_id"]
    set_chain_policy(db, writer=2, floor=2)
    e1 = ev("dev-a1", 1, GEN, a["token"], shp["id"], "PROVISIONAL_ACCEPT", snapshot_id=sid)               # queued by an old writer
    e2 = ev("dev-a1", 2, e1["hash"], b["token"], shp["id"], "LOCAL_BLOCK", snapshot_id=sid)
    r = sync(client, auth, "dev-a1", sid, [e1, e2]).json()
    assert [x["outcome"] for x in r["results"]] == ["CONFLICT", "BLOCKED"] and "CHAIN_VERSION_BELOW_FLOOR" in r["results"][0]["note"]
    assert r["chain"]["server_seq"] == 2 and r["chain"]["min_version"] == 2                                # the chain is not stranded
    assert db.scalar(select(func.count()).select_from(m.Load)) == 0 and r["results"][0]["segregation_task_id"]


def test_an_outdated_tab_cannot_regress_a_device_that_already_writes_v2(client, auth, db):
    _, _, _, a, b, shp = setup(client, auth, db)
    sid = snap(client, auth, shp["id"]).json()["snapshot_id"]
    e1 = ev("dev-a1", 1, GEN, a["token"], shp["id"], "PROVISIONAL_ACCEPT", version=2, snapshot_id=sid)
    e2 = ev("dev-a1", 2, e1["hash"], b["token"], shp["id"], "PROVISIONAL_ACCEPT", snapshot_id=sid)       # old bundle, same queue
    r = sync(client, auth, "dev-a1", None, [e1, e2]).json()["results"]
    assert [x["outcome"] for x in r] == ["ACCEPTED", "CONFLICT"] and "CHAIN_VERSION_REGRESSION" in r[1]["note"]


def test_replay_cannot_change_the_version_an_event_was_synced_under(client, auth, db):
    _, _, _, a, _, shp = setup(client, auth, db)
    sid = snap(client, auth, shp["id"]).json()["snapshot_id"]
    e1 = ev("dev-a1", 1, GEN, a["token"], shp["id"], "PROVISIONAL_ACCEPT", snapshot_id=sid)
    sync(client, auth, "dev-a1", sid, [e1])
    relabeled = dict(e1, chain_version=2, snapshot_id=sid)
    relabeled["hash"] = e1["hash"]
    r = sync(client, auth, "dev-a1", sid, [relabeled])
    assert r.status_code == 409 and r.json()["error"]["code"] == "EVENT_CONTENT_CHANGED"


def test_policy_rejects_a_floor_above_the_writer(db):
    s = policies.settings_of(policies.active(db)).model_dump()
    s["offline"] = {"chain_writer_version": 1, "chain_min_version": 2}
    with pytest.raises(ValueError):
        policies.PolicySettings.model_validate(s)


def test_a_floor_raised_while_a_sync_waits_applies_to_that_sync(client, auth, db):
    _, _, _, a, _, shp = setup(client, auth, db)
    sid = snap(client, auth, shp["id"]).json()["snapshot_id"]
    e1 = ev("dev-a1", 1, GEN, a["token"], shp["id"], "PROVISIONAL_ACCEPT", snapshot_id=sid)
    holder = session_factory()()
    holder.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": policies.POLICY_LOCK})                   # a publish in progress
    with cf.ThreadPoolExecutor(1) as ex:
        try:
            fut = ex.submit(lambda: sync(client, auth, "dev-a1", sid, [e1]))
            deadline = time.time() + 10
            while time.time() < deadline:
                with session_factory()() as s:
                    if s.execute(text("SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND NOT granted")).scalar():
                        break
                time.sleep(0.05)
            else:
                raise AssertionError("sync never waited on the policy lock")
            set_chain_policy(holder, writer=2, floor=2)                                                       # commits → releases
        finally:
            holder.close()
        r = fut.result(timeout=60).json()["results"][0]
    assert r["outcome"] == "CONFLICT" and "CHAIN_VERSION_BELOW_FLOOR" in r["note"]


# ── the database refuses to rewrite history ─────────────────────────────────────────────────────────────────────
def _synced(client, auth, db):
    _, _, _, a, _, shp = setup(client, auth, db)
    sid = snap(client, auth, shp["id"]).json()["snapshot_id"]
    e1 = ev("dev-a1", 1, GEN, a["token"], shp["id"], "PROVISIONAL_ACCEPT", snapshot_id=sid)
    sync(client, auth, "dev-a1", sid, [e1])
    return sid, e1


@pytest.mark.parametrize("statement", [
    "UPDATE device_events SET outcome = 'BLOCKED'",
    "UPDATE device_events SET snapshot_id = NULL",
    "UPDATE device_events SET scan_id = NULL",
    "DELETE FROM device_events",
    "UPDATE offline_snapshots SET expires_at = expires_at + interval '1 day'",
    "UPDATE offline_snapshots SET device_id = device_id || 'x'",
    "DELETE FROM offline_snapshots",
    "UPDATE devices SET last_seq = 0, head_hash = repeat('0', 64)",
])
def test_history_is_immutable_in_the_database(client, auth, db, statement):
    _synced(client, auth, db)
    with pytest.raises(IntegrityError):
        db.execute(text(statement)); db.commit()
    db.rollback()


def test_a_checkpoint_must_point_at_a_recorded_event(client, auth, db):
    _synced(client, auth, db)
    with pytest.raises(IntegrityError):
        db.execute(update(m.Device).where(m.Device.id == "dev-a1").values(last_seq=2, head_hash="b" * 64)); db.commit()
    db.rollback()


def test_settled_snapshots_are_frozen(client, auth, db):
    sid, e1 = _synced(client, auth, db)
    r = client.post(f"{API}/offline/snapshots/{sid}/settle", json={"device_id": "dev-a1", "local_seq": 1, "local_head": e1["hash"], "pending": 0},
                    headers=auth("disp.kiran"))
    assert r.status_code == 200
    with pytest.raises(IntegrityError):
        db.execute(text("UPDATE offline_snapshots SET settlement_reason = 'rewritten'")); db.commit()
    db.rollback()
