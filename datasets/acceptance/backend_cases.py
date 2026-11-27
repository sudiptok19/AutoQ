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
    *_, ship = loaded_online(client, auth, db)
    sid = snap(client, auth, ship['id']).json()['snapshot_id']
    sha = db.scalar(select(m.Evidence.sha256).where(m.Evidence.evidence_source=='SYNTHETIC_TEST').limit(1))
    assert sha
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
