from concurrent.futures import ThreadPoolExecutor

import pytest

from database_guard import require_disposable_database

require_disposable_database()
from sqlalchemy import select, text

from app.db import models as m
from app.db.session import session_factory

from tests.test_chain_v2 import ev, set_chain_policy
from tests.test_dispatch import sealed, setup
from tests.test_offline_release_blockers import _waiting_on_locks, close, loaded_online, recover, settle
from tests.test_offline_scanner import GEN, snap, sync
from tests.test_packaging import released_lot
from tests.test_recall import _order

API = '/api/v1'

def depart(client, headers, shipment):
    return client.post(f'{API}/shipments/{shipment}/events', headers=headers, json={'type':'DEPARTED','location':'Test gate'})


def assert_no_departure(db, shipment_id):
    db.expire_all()
    assert db.scalar(select(m.MovementEvent.id).where(
        m.MovementEvent.shipment_id == shipment_id, m.MovementEvent.type == 'DEPARTED'
    )) is None


def hold_lock_then_queue(stmt, params, first, second):
    with session_factory()() as holder, ThreadPoolExecutor(2) as pool:
        holder.execute(text(stmt), params)
        try:
            a = pool.submit(first)
            _waiting_on_locks(1)
            b = pool.submit(second)
            _waiting_on_locks(2)
        finally:
            holder.commit()
        return a.result(timeout=40), b.result(timeout=40)

@pytest.mark.parametrize('settle_first', [True, False])
def test_settlement_and_close_both_commit_orders(client, auth, db, settle_first):
    *_, ship = loaded_online(client, auth, db)
    sid = snap(client, auth, ship['id']).json()['snapshot_id']
    assert_full_loads(db, ship['id'])
    auth('disp.kiran')
    calls = [lambda: settle(client, auth, sid), lambda: close(client, auth, ship['id'])]
    first, second = hold_lock_then_queue('SELECT id FROM shipments WHERE id=:k FOR UPDATE',
                                        {'k':ship['id']}, *(calls if settle_first else calls[::-1]))
    if settle_first:
        assert first.status_code == second.status_code == 200
    else:
        assert first.status_code == 409 and first.json()['error']['code'] == 'MANIFEST_NOT_RECONCILED', first.json()
        assert {b['code'] for b in first.json()['error']['details']['blockers']} == {'SNAPSHOT_UNSETTLED'}, first.json()
        assert second.status_code == 200, second.json()
        reply = close(client, auth, ship['id'])
        assert reply.status_code == 200, (first.json(), reply.json())

def closed_load(client, auth, db):
    *_, ship = loaded_online(client, auth, db)
    assert_full_loads(db, ship['id'])
    assert close(client, auth, ship['id']).status_code == 200
    unit = db.scalar(select(m.PackageItem.unit_id).join(m.Load, m.Load.package_id == m.PackageItem.package_id).where(m.Load.shipment_id == ship['id']).order_by(m.PackageItem.unit_id).limit(1))
    return ship['id'], unit

def recall_heat(client, headers):
    return client.post(f'{API}/recalls', headers=headers,
                       json={'scope':{'type':'HEAT','code':'H-7781'},'reason':'Disposable QA recall drill'})

@pytest.mark.parametrize('recall_first', [True,False])
def test_recall_and_departure_both_commit_orders(client, auth, db, recall_first):
    ship, unit = closed_load(client, auth, db)
    dispatcher, quality = auth('disp.kiran'), auth('qm.farhan')
    calls = [lambda: recall_heat(client, quality), lambda: depart(client, dispatcher, ship)]
    first, second = hold_lock_then_queue('SELECT id FROM units WHERE id=:k FOR UPDATE',
                                        {'k':unit}, *(calls if recall_first else calls[::-1]))
    if recall_first:
        assert first.status_code == 201 and first.json()['unit_counts']['loaded_not_departed'] > 0
        assert second.status_code == 409 and second.json()['error']['code'] == 'DEPARTURE_NOT_RECONCILED'
    else:
        assert first.status_code == 200
        if second.status_code == 409:
            assert second.json()['error']['code'] == 'SCOPE_CHANGED'
            second = recall_heat(client, quality)
        assert second.status_code == 201 and second.json()['unit_counts']['in_transit'] > 0
        assert {t['type'] for t in second.json()['segregation_tasks']} == {'IN_TRANSIT_RECALL'}

def test_departure_requires_a_nonempty_current_manifest(client, auth, db):
    ship, _ = closed_load(client, auth, db)
    quality, dispatcher = auth('qm.farhan'), auth('disp.kiran')
    recall = recall_heat(client, quality)
    assert recall.status_code == 201
    for task in recall.json()['segregation_tasks']:
        r = client.post(f"{API}/segregation-tasks/{task['id']}/complete", headers=quality, json={'reason':'Synthetic exercise: entire load returned to dock'})
        assert r.status_code == 200
    assert db.scalar(select(m.Load.id).where(m.Load.shipment_id == ship, m.Load.removed_at.is_(None))) is None
    result = depart(client, dispatcher, ship)
    assert result.status_code == 409, f'Empty load departure returned {result.status_code}'
    assert_no_departure(db, ship)

def test_refresh_keeps_requested_recovery_unsettled(client, auth, db):
    *_, ship = loaded_online(client, auth, db)
    sid = snap(client, auth, ship['id']).json()['snapshot_id']
    assert recover(client, auth, sid).json()['recovery_requested_at']
    result = client.get(f'{API}/offline/snapshot', params={'shipment_id':ship['id'],'device_id':'dev-a1','local_seq':0,'local_head':GEN,'pending':0}, headers=auth('disp.kiran'))
    assert result.status_code in (200, 403, 409)
    db.expire_all()
    assert db.get(m.OfflineSnapshot, sid).settlement is None

def test_sync_first_policy_lock_orders_floor_publication(client, auth, db, monkeypatch):
    from threading import Event
    from app.features.offline_scanner import service
    _, _, _, a, b, ship = setup(client, auth, db)
    sid = snap(client, auth, ship['id']).json()['snapshot_id']
    one = ev('dev-a1', 1, GEN, a['token'], ship['id'], 'PROVISIONAL_ACCEPT', snapshot_id=sid)
    two = ev('dev-a1', 2, one['hash'], b['token'], ship['id'], 'PROVISIONAL_ACCEPT', snapshot_id=sid)
    entered, release = Event(), Event()
    original = service._snapshot_reasons
    def pause(*args):
        entered.set()
        assert release.wait(15)
        return original(*args)
    def publish():
        with session_factory()() as writer:
            set_chain_policy(writer, 2, 2)
    monkeypatch.setattr(service, '_snapshot_reasons', pause)
    auth('disp.kiran')
    with ThreadPoolExecutor(2) as pool:
        f = pool.submit(sync, client, auth, 'dev-a1', sid, [one])
        try:
            assert entered.wait(10)
            p = pool.submit(publish)
            _waiting_on_locks(1)
        finally:
            release.set()
        assert f.result(timeout=20).json()['results'][0]['outcome'] == 'ACCEPTED'
        p.result(timeout=20)
    assert sync(client, auth, 'dev-a1', sid, [two]).json()['results'][0]['outcome'] == 'CONFLICT'


def test_ingress_preserves_explicit_null_distinction(client, auth, db):
    _, _, _, a, _, ship = setup(client, auth, db)
    assert snap(client, auth, ship["id"]).status_code == 200
    event = ev("dev-a1", 1, GEN, a["token"], ship["id"], "ONLINE", version=2)
    missing = {k: v for k, v in event.items() if k != "snapshot_id"}
    response = sync(client, auth, "dev-a1", None, [missing])
    assert response.status_code in (400, 409, 422) or response.json()["chain"]["ok"] is False
    assert db.get(m.Device, "dev-a1").last_seq == 0
    assert db.get(m.DeviceEvent, ("dev-a1", 1)) is None
    declared = sync(client, auth, "dev-a1", None, [event]).json()
    assert declared["chain"]["server_seq"] == 1
    assert declared["results"][0]["outcome"] == "BLOCKED"


def test_recovery_requires_reconciliation_type_evidence(client, auth, db):
    from app.core.storage import put_evidence
    from app.features.console_demo.synthetic import render

    *_, ship = loaded_online(client, auth, db)
    sid = snap(client, auth, ship['id']).json()['snapshot_id']
    requested = recover(client, auth, sid)
    assert requested.status_code == 200 and requested.json()['recovery_requested_at']
    qe_id = db.scalar(select(m.User.id).where(m.User.username == "qe.ananya"))
    sha = put_evidence(db, render("good", 39913), "image/jpeg", "SYNTHETIC_TEST", qe_id)
    db.commit()
    result = recover(client, auth, sid, evidence=[sha])
    assert result.status_code in (200, 400, 403, 409, 422), result.status_code
    db.expire_all()
    assert db.get(m.OfflineSnapshot, sid).settlement is None, 'Unrelated synthetic record settled recovery'


def test_policy_floor_cannot_be_lowered_after_cutover(client, auth, db):
    set_chain_policy(db, 2, 2)
    headers = auth("admin")
    current = client.get(f"{API}/policies", headers=headers).json()["settings"]
    current["offline"]["chain_min_version"] = 1
    rejected = client.put(f"{API}/policies", headers=headers, json=current)
    assert rejected.status_code in (400, 409, 422)
    assert client.get(f"{API}/policies", headers=headers).json()["settings"]["offline"]["chain_min_version"] == 2


def test_departure_rechecks_seal_age(client, auth, db):
    from datetime import datetime, UTC, timedelta
    from sqlalchemy import update
    ship, _ = closed_load(client, auth, db)
    pkg_ids = list(db.scalars(select(m.Load.package_id).where(m.Load.shipment_id == ship)))
    db.execute(update(m.SealCheck).where(m.SealCheck.package_id.in_(pkg_ids),m.SealCheck.kind=='RECHECK').values(created_at=datetime.now(UTC)-timedelta(days=2)))
    db.execute(update(m.SealCheck).where(m.SealCheck.package_id.in_(pkg_ids),m.SealCheck.kind=='ENROLL').values(created_at=datetime.now(UTC)-timedelta(days=3)))
    db.commit()
    r = depart(client, auth('disp.kiran'), ship)
    assert r.status_code == 409, f'Stale seal departure returned {r.status_code}'
    assert_no_departure(db, ship)

def test_superseded_snapshot_conflicts_on_first_receipt(client, auth, db):
    _, _, _, a, _, ship = setup(client, auth, db)
    sid = snap(client, auth, ship['id']).json()['snapshot_id']
    one = ev('dev-a1', 1, GEN, a['token'], ship['id'], 'PROVISIONAL_ACCEPT', version=2, snapshot_id=sid)
    assert client.get(f'{API}/offline/snapshot', params={'shipment_id':ship['id'],'device_id':'dev-a1','local_seq':0,'local_head':GEN,'pending':0}, headers=auth('disp.kiran')).status_code == 200
    assert db.get(m.OfflineSnapshot, sid).settlement == 'SUPERSEDED'
    result = sync(client, auth, 'dev-a1', None, [one]).json()['results'][0]
    assert result['outcome'] == 'CONFLICT' and 'SNAPSHOT_SETTLED' in result['note'] and result['segregation_task_id']
    assert db.scalar(select(m.Load.id)) is None

def test_sync_request_is_bounded_before_advancing_checkpoint(client, auth, db):
    _, _, _, a, _, ship = setup(client, auth, db)
    sid = snap(client, auth, ship['id']).json()['snapshot_id']
    e = ev('dev-a1', 1, GEN, a['token'], ship['id'], 'PROVISIONAL_ACCEPT', version=2, snapshot_id=sid)
    assert sync(client, auth, 'dev-a1', None, [e] * 501).status_code == 422
    assert db.get(m.Device, 'dev-a1').last_seq == 0 and db.scalar(select(m.DeviceEvent.seq)) is None


def test_cross_shipment_snapshot_claim_stays_unvalidated(client, auth, db):
    _, _, order, a, _, ship = setup(client, auth, db)
    second = client.post(f'{API}/shipments', headers=auth('disp.kiran'),
        json={'order_id':order['id'],'vehicle_no':'ISOLATED-B','dock':'Test dock'}).json()
    sid = snap(client, auth, second['id']).json()['snapshot_id']
    one = ev('dev-a1', 1, GEN, a['token'], ship['id'], 'PROVISIONAL_ACCEPT', version=2, snapshot_id=sid)
    result = sync(client, auth, 'dev-a1', None, [one]).json()['results'][0]
    assert result['outcome'] == 'CONFLICT' and result['segregation_task_id']
    stored = db.get(m.DeviceEvent, ('dev-a1', 1))
    assert stored.claimed_snapshot_id == sid and stored.snapshot_id is None

def test_alert_must_reference_the_target_spc_inspection(client, auth, db):
    from app.features.alerts.service import raise_alert
    from app.features.console_demo.synthetic import render
    from tests.test_inspection_passports import lot_with_units
    _, units, _ = lot_with_units(client, auth, db, n=4)
    accepted = client.post(f'{API}/inspections', headers=auth('op.ravi'),
        data={'unit_id':units[0]['id'],'evidence_source':'SYNTHETIC_TEST'},
        files={'image':('synthetic.jpg',render('good',1),'image/jpeg')})
    assert accepted.status_code in (200,201)
    alert_id = raise_alert(db, type='QUARANTINE', severity='HIGH', entity_type='UNIT', entity_id=units[1]['id'], reason_codes=['QUARANTINED'], details={}, dedupe_key='verify-unrelated-alert')
    db.commit()
    result = client.post(f'{API}/spc-reviews', headers=auth('qe.ananya'),
        json={'inspection_id':accepted.json()['id'],'alert_id':alert_id})
    assert result.status_code in (400, 409, 422), f'Unrelated alert link returned {result.status_code}'


def assert_full_loads(db, shipment_id):
    from sqlalchemy import func
    loads, units = db.execute(select(func.count(m.Load.id), func.coalesce(func.sum(m.Load.unit_count), 0)).where(
        m.Load.shipment_id == shipment_id, m.Load.removed_at.is_(None)
    )).one()
    shipment = db.get(m.Shipment, shipment_id)
    expected = db.get(m.Order, shipment.order_id).qty_units
    if (loads, units) != (2, expected):
        scan_details = db.execute(select(m.Scan.decision, m.Scan.reasons).where(m.Scan.shipment_id == shipment_id)).all()
        pytest.fail(f"Fixture failed before the race: loads={loads}, units={units}/{expected}, scan decisions={scan_details}")

def test_original_recall_fixture_reports_the_gate(client, auth, db):
    c, units, _, _ = released_lot(client, auth, db)
    order = _order(client, auth, db, c["part"]["id"], "SO-RACE", 4)
    pkg = sealed(client, auth, order["id"], [u["id"] for u in units[:4]], seed=70)
    ship = client.post(f"{API}/shipments", headers=auth("disp.kiran"), json={
        "order_id": order["id"], "vehicle_no": "WB-race-ship", "dock": "Dock 3",
    })
    assert ship.status_code == 201, ship.json()
    scan = client.post(f"{API}/shipments/{ship.json()['id']}/scans", headers=auth("disp.kiran"), json={
        "token": pkg["token"], "client_event_id": "race-ship",
    })
    assert scan.status_code in (200, 201), scan.json()
    result = scan.json()
    assert result["decision"] == "ACCEPTED", {"reasons": result["reasons"], "checks": result["checks"]}


def test_legacy_drain_while_writer_is_v2_cannot_authorize_load(client, auth, db):
    _, _, _, a, _, ship = setup(client, auth, db)
    sid = snap(client, auth, ship["id"]).json()["snapshot_id"]
    set_chain_policy(db, 2, 1)
    queued = ev("dev-a1", 1, GEN, a["token"], ship["id"], "PROVISIONAL_ACCEPT", snapshot_id=sid)
    result = sync(client, auth, "dev-a1", sid, [queued]).json()
    assert result["chain"]["server_seq"] == 1
    assert result["results"][0]["outcome"] == "CONFLICT"
    assert result["results"][0]["segregation_task_id"]
    assert db.scalar(select(m.Load.id)) is None


def recovery_sessions(client, auth, db):
    _, _, order, _, _, ship = loaded_online(client, auth, db)
    second = client.post(
        f"{API}/shipments", headers=auth("disp.kiran"),
        json={"order_id": order["id"], "vehicle_no": "ISOLATED-R2", "dock": "Test dock B"},
    )
    assert second.status_code == 201, second.json()
    sessions = []
    for current, device in ((ship, "dev-a1"), (second.json(), "dev-b2")):
        issued = snap(client, auth, current["id"], device=device)
        assert issued.status_code == 200, issued.json()
        sid = issued.json()["snapshot_id"]
        pending = recover(client, auth, sid)
        assert pending.status_code == 200 and pending.json()["settlement"] is None
        assert pending.json()["recovery_task_id"]
        sessions.append((current, sid, pending.json()["recovery_task_id"]))
    return sessions


def upload_review_record(client, auth, summary, *, uploader="qe.ananya", purpose="REVIEW_RECORD"):
    # Real upload path and a deterministic synthetic document, with no physical-verification data.
    from io import BytesIO
    from reportlab.pdfgen.canvas import Canvas

    stream = BytesIO()
    page = Canvas(stream, invariant=1)
    page.drawString(60, 750, "SYNTHETIC TEST RECORD")
    page.drawString(60, 720, summary)
    page.save()
    response = client.post(
        f"{API}/evidence", headers=auth(uploader),
        files={"file": ("test-record.pdf", stream.getvalue(), "application/pdf")},
        data={"purpose": purpose},
    )
    assert response.status_code in (200, 201), response.json()
    return response.json()["sha256"]


@pytest.mark.parametrize("uploader", ["qe.ananya", "qm.farhan"])
def test_r2_unrelated_qualifying_upload_does_not_settle_recovery(client, auth, db, uploader):
    (_, sid, task_id), _ = recovery_sessions(client, auth, db)
    sha = upload_review_record(
        client, auth, "Unrelated supplier meeting minutes", uploader=uploader, purpose="SUPPLIER_DOCUMENT",
    )
    result = recover(client, auth, sid, evidence=[sha])
    db.expire_all()
    assert db.get(m.OfflineSnapshot, sid).settled_at is None, f"Unrelated record settled recovery ({result.status_code})"
    assert db.get(m.SegregationTask, task_id).status == "OPEN"


def test_r2_record_for_other_shipment_cannot_settle_recovery(client, auth, db):
    from datetime import timedelta
    import time
    from sqlalchemy import func

    (ship_a, sid_a, task_a), (ship_b, sid_b, task_b) = recovery_sessions(client, auth, db)
    latest_request = max(db.get(m.OfflineSnapshot, sid).recovery_requested_at for sid in (sid_a, sid_b))
    # Compare the database clock with both request timestamps before uploading. An older record would
    # be rejected by the time check alone and would reveal nothing about shipment association.
    deadline = time.monotonic() + 10
    while db.scalar(select(func.clock_timestamp())) <= latest_request + timedelta(milliseconds=100):
        if time.monotonic() >= deadline:
            pytest.fail("Database and API clocks did not converge for the evidence fixture")
        time.sleep(0.05)
    sha = upload_review_record(client, auth, f"Recount for {ship_a['code']} {sid_a} task {task_a}")
    assert db.get(m.Evidence, sha).created_at >= latest_request
    assert ship_a["id"] != ship_b["id"]
    rejected = recover(client, auth, sid_b, evidence=[sha])
    db.expire_all()
    assert db.get(m.OfflineSnapshot, sid_b).settled_at is None, f"Other shipment settled ({rejected.status_code})"
    assert db.get(m.SegregationTask, task_b).status == "OPEN"


def test_r2_same_record_has_one_concurrent_consumer(client, auth, db, monkeypatch):
    from threading import Event, Lock
    from app.features.offline_scanner import service

    first, second = recovery_sessions(client, auth, db)
    sha = upload_review_record(client, auth, f"Recount for {first[0]['code']} {first[1]} task {first[2]}")
    auth("qm.farhan")
    both_read, release = Event(), Event()
    count_lock = Lock()
    readers = 0
    original = service._recovery_evidence

    def pause_after_unscoped_read(*args):
        nonlocal readers
        record = original(*args)
        if record.get("for_snapshot") and not record.get("recovery_task_id"):
            with count_lock:
                readers += 1
                if readers == 2:
                    both_read.set()
            if not release.wait(12):
                raise RuntimeError("Timed out releasing the recovery evidence readers")
        return record

    monkeypatch.setattr(service, "_recovery_evidence", pause_after_unscoped_read)
    with ThreadPoolExecutor(2) as pool:
        futures = [pool.submit(recover, client, auth, sid, evidence=[sha]) for _, sid, _ in (first, second)]
        # A corrected serializing or scoped reader may only admit one request to the pause.
        both_read.wait(4)
        release.set()
        replies = [future.result(timeout=30) for future in futures]
    db.expire_all()
    consumers = [sid for _, sid, _ in (first, second) if db.get(m.OfflineSnapshot, sid).settlement == "RECOVERED"]
    assert consumers == [first[1]], f"{len(consumers)} recoveries settled, HTTP {[r.status_code for r in replies]}"


def test_r2_two_step_recovery_and_sequential_reuse(client, auth, db):
    (ship, sid, task), (other, other_sid, _) = recovery_sessions(client, auth, db)
    assert close(client, auth, ship["id"]).status_code == 409
    sha = upload_review_record(client, auth, f"Recount for {ship['code']} {sid}, task {task}")
    result = recover(client, auth, sid, evidence=[sha])
    assert result.status_code == 200 and result.json()["settlement"] == "RECOVERED", result.json()
    assert result.json()["recovery_evidence"][0]["sha256"] == sha
    reused = recover(client, auth, other_sid, evidence=[sha])
    assert reused.status_code == 422 and reused.json()["error"]["code"] == "EVIDENCE_NOT_LINKED"
    db.expire_all()
    assert db.get(m.SegregationTask, task).status == "COMPLETED"
    assert db.get(m.OfflineSnapshot, other_sid).settled_at is None
    assert close(client, auth, ship["id"]).status_code == 200


def test_r2_fresh_truck_recheck_allows_one_departure(client, auth, db):
    from datetime import datetime, UTC, timedelta
    from sqlalchemy import update
    from tests.test_packaging import check

    *_, a, b, shipment = loaded_online(client, auth, db)
    ship = shipment["id"]
    assert_full_loads(db, ship)
    assert close(client, auth, ship).status_code == 200
    packages = [(a["id"], 1), (b["id"], 11)]
    pkg_ids = [pkg_id for pkg_id, _ in packages]
    for kind, days in (("ENROLL", 3), ("RECHECK", 2)):
        db.execute(update(m.SealCheck).where(m.SealCheck.package_id.in_(pkg_ids), m.SealCheck.kind == kind)
                   .values(created_at=datetime.now(UTC) - timedelta(days=days)))
    db.commit()
    blocked = depart(client, auth("disp.kiran"), ship)
    assert blocked.status_code == 409
    for pkg_id, seed in packages:
        result = check(client, auth, pkg_id, "intact", seed)
        assert result.status_code in (200, 201) and result.json()["result"] == "INTACT", result.json()
    released = depart(client, auth("disp.kiran"), ship)
    assert released.status_code == 200 and len(released.json()["manifest_sha256"]) == 64, released.json()
    assert depart(client, auth("disp.kiran"), ship).status_code == 409


def test_r2_competing_online_scan_is_not_adopted_by_offline_reconciliation(client, auth, db, monkeypatch):
    from threading import Event
    from tests.test_dispatch import scan
    from app.features.offline_scanner import service

    _, _, _, pkg, _, ship = setup(client, auth, db)
    sid = snap(client, auth, ship["id"]).json()["snapshot_id"]
    assert settle(client, auth, sid).status_code == 200
    capture = ev("dev-a1", 1, GEN, pkg["token"], ship["id"], "PROVISIONAL_ACCEPT", version=2, snapshot_id=sid)
    reached, release = Event(), Event()
    original = service._snapshot_reasons

    def pause_after_provenance_read(*args):
        reached.set()
        if not release.wait(15):
            raise RuntimeError("Timed out waiting for the online scan")
        return original(*args)

    monkeypatch.setattr(service, "_snapshot_reasons", pause_after_provenance_read)
    auth("disp.kiran")
    with ThreadPoolExecutor(1) as pool:
        pending = pool.submit(sync, client, auth, "dev-a1", None, [capture])
        try:
            assert reached.wait(10)
            online = scan(client, auth, ship["id"], pkg["token"], capture["client_event_id"])
            assert online.status_code == 200 and online.json()["decision"] == "ACCEPTED", online.json()
        finally:
            release.set()
        reconciled = pending.result(timeout=20).json()["results"][0]
    assert reconciled["outcome"] == "CONFLICT" and reconciled["segregation_task_id"], reconciled
    db.expire_all()
    assert db.get(m.DeviceEvent, ("dev-a1", 1)).scan_id is None
    loads = db.scalars(select(m.Load).where(m.Load.shipment_id == ship["id"], m.Load.removed_at.is_(None))).all()
    assert len(loads) == 1 and loads[0].accepted_scan_id == online.json()["scan_id"]


def test_r2_online_cannot_adopt_the_offline_scan_that_won_first(client, auth, db, monkeypatch):
    from threading import Event
    from app.features.dispatch import service
    from tests.test_dispatch import scan

    _, _, _, pkg, _, ship = setup(client, auth, db)
    sid = snap(client, auth, ship["id"]).json()["snapshot_id"]
    capture = ev("dev-a1", 1, GEN, pkg["token"], ship["id"], "PROVISIONAL_ACCEPT", version=2, snapshot_id=sid)
    reached, release = Event(), Event()
    original = service.evaluate

    def pause_first_gate(*args):
        if not reached.is_set():
            reached.set()
            if not release.wait(15):
                raise RuntimeError("Timed out waiting for the offline scan")
        return original(*args)

    monkeypatch.setattr(service, "evaluate", pause_first_gate)
    auth("disp.kiran")
    with ThreadPoolExecutor(1) as pool:
        pending = pool.submit(scan, client, auth, ship["id"], pkg["token"], capture["client_event_id"])
        try:
            assert reached.wait(10)
            first = sync(client, auth, "dev-a1", None, [capture]).json()["results"][0]
            assert first["outcome"] == "ACCEPTED", first
        finally:
            release.set()
        competing = pending.result(timeout=20)
    assert competing.status_code == 409, "A separate online request must not inherit the offline scan"


def test_r2_late_conflict_after_departure_gets_in_transit_instruction(client, auth, db, monkeypatch):
    from app.features.offline_scanner import service
    from tests.test_offline_release_blockers import _race

    captures = []
    apply_event = service._apply

    def record_submission(principal, device_id, event):
        captures.append((device_id, dict(event)))
        return apply_event(principal, device_id, event)

    monkeypatch.setattr(service, "_apply", record_submission)
    out, ship = _race(client, auth, db, first="depart")
    assert out["depart"].status_code == 200 and ship.status == "IN_TRANSIT"
    conflict = out["sync"].json()["results"][0]
    assert conflict["outcome"] == "CONFLICT" and conflict["segregation_task_id"]
    task = db.get(m.SegregationTask, conflict["segregation_task_id"])
    assert task.shipment_id == ship.id
    device_id, event = captures[0]
    replay = sync(client, auth, device_id, None, [event]).json()["results"][0]
    assert replay["segregation_task_id"] == task.id and replay["note"] == conflict["note"]
    assert task.consignee_action.get("action") and "physically unload" not in task.reason.lower(), task.reason


def test_r2_replay_preserves_pre_r2_v1_effective_batch_claim(client, auth, db):
    _, _, _, pkg, _, ship = setup(client, auth, db)
    sid = snap(client, auth, ship["id"]).json()["snapshot_id"]
    original_wire = ev("dev-a1", 1, GEN, pkg["token"], ship["id"], "ONLINE", snapshot_id=None)
    original_wire["snapshot_id"] = None
    # Reproduce the durable result of the pre-R2 fallback, which replaced explicit null with the batch claim.
    pre_r2_effective_event = {**original_wire, "snapshot_id": sid}
    initial = sync(client, auth, "dev-a1", sid, [pre_r2_effective_event])
    assert initial.status_code == 200
    assert db.get(m.DeviceEvent, ("dev-a1", 1)).claimed_snapshot_id == sid
    response = sync(client, auth, "dev-a1", sid, [original_wire])
    assert response.status_code == 200 and response.json()["results"][0]["outcome"] == "DUPLICATE"


def test_r2_losing_both_online_and_first_sync_replies_preserves_decision(client, auth, db):
    _, _, _, pkg, _, ship = setup(client, auth, db)
    assert snap(client, auth, ship["id"]).status_code == 200
    capture = ev("dev-a1", 1, GEN, pkg["token"], ship["id"], "ONLINE", version=2)
    online = client.post(
        f"{API}/shipments/{ship['id']}/scans", headers=auth("disp.kiran"),
        json={"token": pkg["token"], "client_event_id": capture["client_event_id"], "device_id": "dev-a1"},
    ).json()
    assert online["decision"] == "ACCEPTED"
    for _ in range(2):
        result = sync(client, auth, "dev-a1", None, [capture]).json()["results"][0]
        assert result["outcome"] == "DUPLICATE" and result["original_outcome"] == "ACCEPTED", result["original_outcome"]
        assert result["scan"]["scan_id"] == online["scan_id"]


def test_r2_writer_switch_keeps_preexisting_v1_acceptance_replayable(client, auth, db):
    _, _, _, pkg, _, ship = setup(client, auth, db)
    sid = snap(client, auth, ship["id"]).json()["snapshot_id"]
    queued = ev("dev-a1", 1, GEN, pkg["token"], ship["id"], "PROVISIONAL_ACCEPT", snapshot_id=sid)
    first = sync(client, auth, "dev-a1", None, [queued]).json()["results"][0]
    assert first["outcome"] == "ACCEPTED"
    set_chain_policy(db, 2, 2)
    replay = sync(client, auth, "dev-a1", None, [queued]).json()["results"][0]
    assert replay["outcome"] == "DUPLICATE" and replay["original_outcome"] == "ACCEPTED"
    assert replay["scan"]["scan_id"] == first["scan"]["scan_id"]
