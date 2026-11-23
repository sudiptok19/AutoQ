"""Dispatch gate, manifest close, departure re-validation and races on real PostgreSQL."""
import concurrent.futures as cf

from sqlalchemy import func, select

from app.db import models as m
from tests.test_packaging import add, check, new_pkg, released_lot, seal

API = "/api/v1"


def sealed(client, auth, order_id, unit_ids, seed=0, recheck=True):
    p = new_pkg(client, auth, order_id)
    for u in unit_ids:
        assert add(client, auth, p["id"], u).status_code == 200
    s = seal(client, auth, p["id"], seed=seed).json()
    if recheck:
        assert check(client, auth, p["id"], "intact", seed + 1).json()["result"] == "INTACT"
    return s


def setup(client, auth, db, qty=8):
    c, units, sample, order = released_lot(client, auth, db)
    if qty != 8:
        db.get(m.Order, order["id"]).qty_units = qty; db.commit()
    a = sealed(client, auth, order["id"], [u["id"] for u in units[:4]], seed=0)
    b = sealed(client, auth, order["id"], [u["id"] for u in units[4:8]], seed=10)
    shp = client.post(f"{API}/shipments", json={"order_id": order["id"], "vehicle_no": "WB-04-BC-1186", "dock": "Dock 3"}, headers=auth("disp.kiran"))
    assert shp.status_code == 201, shp.text
    return c, units, order, a, b, shp.json()


def scan(client, auth, shp, token, eid, who="disp.kiran"):
    return client.post(f"{API}/shipments/{shp}/scans", json={"token": token, "client_event_id": eid, "device_id": "web-test"}, headers=auth(who))


def test_happy_path_load_close_depart_deliver(client, auth, db):
    _, _, order, a, b, shp = setup(client, auth, db)
    r = scan(client, auth, shp["id"], a["token"], "e1").json()
    assert r["decision"] == "ACCEPTED" and [c["status"] for c in r["checks"]] == ["PASS"] * 11 and r["shipment_progress"]["loaded_units"] == 4
    assert scan(client, auth, shp["id"], b["token"], "e2").json()["decision"] == "ACCEPTED"
    early = client.post(f"{API}/shipments/{shp['id']}/events", json={"type": "DEPARTED", "location": "Gate"}, headers=auth("disp.kiran"))
    assert early.status_code == 409 and early.json()["error"]["code"] == "MANIFEST_OPEN"
    closed = client.post(f"{API}/shipments/{shp['id']}/close", headers=auth("disp.kiran")).json()
    assert closed["status"] == "CLOSED" and closed["loaded_units"] == 8
    dep = client.post(f"{API}/shipments/{shp['id']}/events", json={"type": "DEPARTED", "location": "Plant gate"}, headers=auth("disp.kiran")).json()
    assert dep["status"] == "IN_TRANSIT" and len(dep["manifest_sha256"]) == 64
    assert client.get(f"{API}/packages/{a['id']}", headers=auth("op.ravi")).json()["state"] == "IN_TRANSIT"
    client.post(f"{API}/shipments/{shp['id']}/events", json={"type": "CHECKPOINT", "location": "NH-19 toll"}, headers=auth("disp.kiran"))
    done = client.post(f"{API}/shipments/{shp['id']}/events", json={"type": "DELIVERED", "location": "Gurugram Gate 4"}, headers=auth("disp.kiran")).json()
    assert done["status"] == "DELIVERED" and [x["type"] for x in done["movements"]] == ["DEPARTED", "CHECKPOINT", "DELIVERED"]
    assert client.post(f"{API}/shipments/{shp['id']}/events", json={"type": "DELIVERED"}, headers=auth("disp.kiran")).status_code == 409


def test_idempotent_replay_and_reused_event_id(client, auth, db):
    _, _, _, a, b, shp = setup(client, auth, db)
    first = scan(client, auth, shp["id"], a["token"], "dev:1").json()
    again = scan(client, auth, shp["id"], a["token"], "dev:1").json()
    assert again["replay"] is True and again["scan_id"] == first["scan_id"] and again["shipment_progress"]["loaded_units"] == 4
    reuse = scan(client, auth, shp["id"], b["token"], "dev:1")
    assert reuse.status_code == 409 and reuse.json()["error"]["code"] == "EVENT_ID_REUSED"
    dup = scan(client, auth, shp["id"], a["token"], "dev:2").json()
    assert dup["decision"] == "BLOCKED" and dup["reasons"][0]["code"] == "ALREADY_LOADED"
    assert db.scalar(select(func.count()).select_from(m.Load)) == 1


def test_forged_stale_and_wrong_kind_tokens(client, auth, db):
    _, units, _, a, _, shp = setup(client, auth, db)
    h, p, s = a["token"].split(".")
    forged = scan(client, auth, shp["id"], f"{h}.{p[:-2]}{'B' if p[-2] == 'A' else 'A'}{p[-1]}.{s}", "f1").json()
    assert forged["decision"] == "BLOCKED" and forged["checks"][0]["status"] == "FAIL" and {c["status"] for c in forged["checks"][1:]} <= {"SKIP", "PASS"}
    pp = client.get(f"{API}/units/{units[0]['id']}/detail", headers=auth("op.ravi")).json()["passport"]["token"]
    assert scan(client, auth, shp["id"], pp, "f2").json()["reasons"][0]["code"] == "TOKEN_MISMATCH"
    client.post(f"{API}/packages/{a['id']}/unpack", json={"reason": "reseal"}, headers=auth("op.ravi"))
    seal(client, auth, a["id"], seed=5)
    stale = scan(client, auth, shp["id"], a["token"], "f3").json()
    assert stale["reasons"][0]["code"] == "TOKEN_MISMATCH" and "superseded" in stale["reasons"][0]["message"]
    assert db.scalar(select(func.count()).select_from(m.Scan).where(m.Scan.decision == "BLOCKED")) == 3
    assert db.scalars(select(m.Alert).where(m.Alert.type == "COUNTERFEIT_SUSPECT")).first() is not None


def test_seal_gates(client, auth, db):
    c, units, order, _, _, shp = setup(client, auth, db, qty=16)
    no_recheck = sealed(client, auth, order["id"], [units[8]["id"]], seed=20, recheck=False)
    assert scan(client, auth, shp["id"], no_recheck["token"], "s1").json()["reasons"][0]["code"] == "SEAL_RECHECK_REQUIRED"
    check(client, auth, no_recheck["id"], "torn", 21)
    assert scan(client, auth, shp["id"], no_recheck["token"], "s2").json()["reasons"][0]["code"] == "SEAL_SUSPICIOUS"


def test_wrong_order_and_quantity(client, auth, db):
    c, units, order, a, b, shp = setup(client, auth, db, qty=4)
    assert scan(client, auth, shp["id"], a["token"], "q1").json()["decision"] == "ACCEPTED"
    over = scan(client, auth, shp["id"], b["token"], "q2").json()
    assert over["reasons"][0]["code"] == "ORDER_QTY_EXCEEDED"
    other = client.post(f"{API}/orders", json={"order_no": "SO-2", "customer_id": db.scalars(select(m.Customer)).first().id, "part_id": c["part"]["id"],
                                               "qty_units": 4, "destination": "Kolkata"}, headers=auth("admin")).json()
    shp2 = client.post(f"{API}/shipments", json={"order_id": other["id"], "vehicle_no": "X", "dock": "Dock 1"}, headers=auth("disp.kiran")).json()
    assert scan(client, auth, shp2["id"], b["token"], "q3").json()["reasons"][0]["code"] == "WRONG_ORDER"


def test_operator_cannot_scan(client, auth, db):
    _, _, _, a, _, shp = setup(client, auth, db)
    assert scan(client, auth, shp["id"], a["token"], "r1", who="op.ravi").status_code == 403


def test_containment_after_sealing_blocks_load(client, auth, db):
    c, _, _, a, _, shp = setup(client, auth, db)
    client.post(f"{API}/four-m-events", json={"category": "MAN", "planned": False, "line_id": c["line"]["id"], "description": "Relief operator", "reason": "Absence"},
                headers=auth("qe.ananya"))
    r = scan(client, auth, shp["id"], a["token"], "c1").json()
    assert {x["code"] for x in r["reasons"]} >= {"CONTAINMENT_ACTIVE"}


def test_two_loaders_race_for_one_package(client, auth, db):
    _, _, order, a, _, shp = setup(client, auth, db)
    shp2 = client.post(f"{API}/shipments", json={"order_id": order["id"], "vehicle_no": "Y", "dock": "Dock 4"}, headers=auth("disp.kiran")).json()
    h = auth("disp.kiran")

    def go(args):
        sid, eid = args
        return client.post(f"{API}/shipments/{sid}/scans", json={"token": a["token"], "client_event_id": eid}, headers=h).json()["decision"]
    with cf.ThreadPoolExecutor(2) as ex:
        results = sorted(ex.map(go, [(shp["id"], "race-1"), (shp2["id"], "race-2")]))
    assert results == ["ACCEPTED", "BLOCKED"]
    assert db.scalar(select(func.count()).select_from(m.Load).where(m.Load.package_id == a["id"])) == 1


def test_close_requires_reconciled_quantity(client, auth, db):
    _, _, _, a, _, shp = setup(client, auth, db)
    scan(client, auth, shp["id"], a["token"], "x1")
    r = client.post(f"{API}/shipments/{shp['id']}/close", headers=auth("disp.kiran"))
    assert r.status_code == 409 and r.json()["error"]["details"]["blockers"][0]["code"] == "QTY_MISMATCH"


def test_hold_after_close_blocks_departure_and_creates_removal_task(client, auth, db):
    c, _, _, a, b, shp = setup(client, auth, db)
    scan(client, auth, shp["id"], a["token"], "d1"); scan(client, auth, shp["id"], b["token"], "d2")
    assert client.post(f"{API}/shipments/{shp['id']}/close", headers=auth("disp.kiran")).json()["status"] == "CLOSED"
    client.post(f"{API}/four-m-events", json={"category": "METHOD", "planned": False, "line_id": c["line"]["id"], "description": "New WI rev", "reason": "Audit"},
                headers=auth("qe.ananya"))
    dep = client.post(f"{API}/shipments/{shp['id']}/events", json={"type": "DEPARTED", "location": "Gate"}, headers=auth("disp.kiran"))
    assert dep.status_code == 409 and dep.json()["error"]["code"] == "DEPARTURE_BLOCKED"
    tids = dep.json()["error"]["details"]["segregation_task_ids"]
    tasks = client.get(f"{API}/segregation-tasks?status=OPEN", headers=auth("disp.kiran")).json()["items"]
    assert {t["id"] for t in tasks} == set(tids) and {t["type"] for t in tasks} == {"REMOVAL_REQUIRED"} and len(tasks) == 2
    again = client.post(f"{API}/shipments/{shp['id']}/close", headers=auth("disp.kiran"))           # shipment reopened; still blocked
    assert again.status_code == 409
    done = client.post(f"{API}/segregation-tasks/{tids[0]}/complete", json={"reason": "Crate unloaded to quarantine cage Q2"}, headers=auth("disp.kiran")).json()
    assert done["status"] == "COMPLETED"
    assert len(client.get(f"{API}/shipments/{shp['id']}", headers=auth("disp.kiran")).json()["loaded_package_ids"]) == 1
    assert db.scalars(select(m.Alert).where(m.Alert.type == "DEPARTURE_BLOCKED")).first().audible_siren
