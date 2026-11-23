"""Trace, mixed-lot recall arithmetic, atomic quarantine, tasks, release and the quarantine-vs-departure race. Real PostgreSQL."""
import concurrent.futures as cf

from sqlalchemy import func, select

from app.db import models as m
from tests.test_dispatch import sealed
from tests.test_inspection_passports import inspect
from tests.test_packaging import released_lot

API = "/api/v1"


def _order(client, auth, db, part_id, no, qty):
    cus = db.scalars(select(m.Customer)).first().id
    return client.post(f"{API}/orders", json={"order_no": no, "customer_id": cus, "part_id": part_id, "qty_units": qty, "destination": "Gurugram"},
                       headers=auth("admin")).json()


def _ship(client, auth, order_id, pkg, eid, *, close=True, depart=False, deliver=False):
    s = client.post(f"{API}/shipments", json={"order_id": order_id, "vehicle_no": f"WB-{eid}", "dock": "Dock 3"}, headers=auth("disp.kiran")).json()
    assert client.post(f"{API}/shipments/{s['id']}/scans", json={"token": pkg["token"], "client_event_id": eid}, headers=auth("disp.kiran")).json()["decision"] == "ACCEPTED"
    if close:
        assert client.post(f"{API}/shipments/{s['id']}/close", headers=auth("disp.kiran")).json()["status"] == "CLOSED"
    if depart:
        client.post(f"{API}/shipments/{s['id']}/events", json={"type": "DEPARTED", "location": "Gate"}, headers=auth("disp.kiran"))
    if deliver:
        client.post(f"{API}/shipments/{s['id']}/events", json={"type": "DELIVERED", "location": "Customer"}, headers=auth("disp.kiran"))
    return s


def world(client, auth, db):
    """Heat H-7781 → lot A (10 units). Heat H-7790 → lot B (4 units). P1 at plant (A), P2 mixed A+B loaded,
    P3 (A) in transit, P4 (A) delivered."""
    c, A, sampleA, _ = released_lot(client, auth, db)
    h2 = client.post(f"{API}/heats", data={"heat_code": "H-7790", "supplier_id": c["supplier"]["id"], "material_grade": "SG 500/7"},
                     files={"certificate": ("c.pdf", b"%PDF-1.4\n%h2\n", "application/pdf")}, headers=auth("admin")).json()
    lotB = client.post(f"{API}/lots", json={"lot_code": "L-2611-B", "part_id": c["part"]["id"], "line_id": c["line"]["id"], "heat_ids": [h2["id"]],
                                            "planned_qty": 4}, headers=auth("op.ravi")).json()
    B = client.post(f"{API}/lots/{lotB['id']}/units", json={"count": 4}, headers=auth("op.ravi")).json()["items"]
    client.post(f"{API}/lots/{lotB['id']}/freeze-and-sample", headers=auth("qe.ananya"))
    for i, u in enumerate(B):
        assert inspect(client, auth, u["id"], seed=900 + i).json()["verdict"] == "PASS"
    part = c["part"]["id"]
    o1, o2, o3, o4 = (_order(client, auth, db, part, f"SO-R{i}", q) for i, q in enumerate([4, 4, 2, 2]))
    ids = [u["id"] for u in A]
    p1 = sealed(client, auth, o1["id"], ids[0:4], seed=30)
    p2 = sealed(client, auth, o2["id"], ids[4:6] + [B[0]["id"], B[1]["id"]], seed=40)
    p3 = sealed(client, auth, o3["id"], ids[6:8], seed=50)
    p4 = sealed(client, auth, o4["id"], ids[8:10], seed=60)
    s2 = _ship(client, auth, o2["id"], p2, "w2")                          # loaded, closed, not departed
    s3 = _ship(client, auth, o3["id"], p3, "w3", depart=True)
    s4 = _ship(client, auth, o4["id"], p4, "w4", depart=True, deliver=True)
    return {"c": c, "A": A, "B": B, "p": [p1, p2, p3, p4], "s": [s2, s3, s4], "orders": [o1, o2, o3, o4], "h2": h2}


def test_preview_counts_each_unit_once_by_physical_location(client, auth, db):
    w = world(client, auth, db)
    pv = client.post(f"{API}/recalls/preview", json={"scope": {"type": "HEAT", "code": "H-7781"}}, headers=auth("qe.ananya")).json()
    assert pv["units"] == 10 and pv["unit_counts"] == {"at_plant": 4, "loaded_not_departed": 2, "in_transit": 2, "delivered": 2}
    p1, p2 = w["p"][0], w["p"][1]
    assert pv["held_package_ids"] == sorted([p1["id"], p2["id"]])
    mixed = next(e for e in pv["loaded_not_departed"] if e["package_id"] == p2["id"])
    assert mixed["mixed"] is True and mixed["units"] == 2 and mixed["package_units"] == 4
    assert len(pv["in_transit"]) == 1 and len(pv["delivered"]) == 1 and pv["preview"] is True
    assert db.scalar(select(func.count()).select_from(m.Recall)) == 0            # preview writes nothing


def test_quarantine_holds_creates_tasks_and_blocks_gate(client, auth, db):
    w = world(client, auth, db)
    body = {"scope": {"type": "HEAT", "code": "H-7781"}, "reason": "Supplier reported nodularity out of spec"}
    assert client.post(f"{API}/recalls", json=body, headers=auth("qe.ananya")).status_code == 403
    r = client.post(f"{API}/recalls", json=body, headers=auth("qm.farhan")).json()
    assert r["unit_counts"]["at_plant"] == 4 and r["executed_by"] == "qm.farhan"
    assert sorted(t["type"] for t in r["segregation_tasks"]) == ["IN_TRANSIT_RECALL", "NOTIFY_CUSTOMER", "REMOVAL_REQUIRED"]
    assert client.get(f"{API}/heats", headers=auth("op.ravi")).json()["items"][0]["status"] == "QUARANTINED"
    a0 = client.get(f"{API}/units/{w['A'][0]['id']}/detail", headers=auth("op.ravi")).json()
    assert a0["passport"]["status"] == "HELD" and "QUARANTINED" in {b["code"] for b in a0["eligibility"]["blockers"]}
    b0 = client.get(f"{API}/units/{w['B'][0]['id']}", headers=auth("op.ravi")).json()     # clean unit inside the mixed package
    assert not b0["eligibility"]["dispatch_eligible"]
    assert client.get(f"{API}/units/{w['B'][2]['id']}", headers=auth("op.ravi")).json()["eligibility"]["dispatch_eligible"] is True
    shp = client.post(f"{API}/shipments", json={"order_id": w["orders"][0]["id"], "vehicle_no": "X", "dock": "D"}, headers=auth("disp.kiran")).json()
    scan = client.post(f"{API}/shipments/{shp['id']}/scans", json={"token": w["p"][0]["token"], "client_event_id": "after"}, headers=auth("disp.kiran")).json()
    assert scan["decision"] == "BLOCKED" and "QUARANTINED" in {x["code"] for x in scan["reasons"]}
    assert db.scalars(select(m.Alert).where(m.Alert.type == "QUARANTINE")).first().audible_siren
    again = client.post(f"{API}/recalls", json=body, headers=auth("qm.farhan")).json()
    assert db.scalar(select(func.count()).select_from(m.SegregationTask).where(m.SegregationTask.type == "REMOVAL_REQUIRED")) == 1
    assert again["id"] != r["id"]


def test_trace_graph_from_heat_and_clean_lineage_of_other_lot(client, auth, db):
    w = world(client, auth, db)
    g = client.get(f"{API}/trace", params={"heat": "H-7781"}, headers=auth("op.ravi")).json()
    types = {n["type"] for n in g["nodes"]}
    assert {"SUPPLIER", "HEAT", "LOT", "PACKAGE", "SHIPMENT", "CUSTOMER"} <= types and g["summary"]["units"] == 10
    assert g["summary"] == {"units": 10, "packages": 4, "at_plant": 4, "loaded": 2, "in_transit": 2, "delivered": 2}
    b = client.get(f"{API}/trace", params={"q": "H-7790"}, headers=auth("op.ravi")).json()
    assert b["summary"]["units"] == 4 and not any(n["label"] == "H-7781" for n in b["nodes"])
    u = client.get(f"{API}/trace", params={"serial": w["A"][0]["serial"]}, headers=auth("op.ravi")).json()
    assert {"UNIT", "PASSPORT"} <= {n["type"] for n in u["nodes"]}
    assert client.get(f"{API}/trace", params={"q": "nope"}, headers=auth("op.ravi")).json()["error"]["code"] == "SCOPE_NOT_FOUND"


def test_release_restores_eligibility(client, auth, db):
    w = world(client, auth, db)
    r = client.post(f"{API}/recalls", json={"scope": {"type": "LOT", "code": "L-2611-B"}, "reason": "precaution"}, headers=auth("qm.farhan")).json()
    assert not client.get(f"{API}/units/{w['B'][2]['id']}", headers=auth("op.ravi")).json()["eligibility"]["dispatch_eligible"]
    rel = client.post(f"{API}/recalls/{r['id']}/release", json={"reason": "Supplier lab retest passed"}, headers=auth("qm.farhan")).json()
    assert rel["released_by"] == "qm.farhan" and rel["passports_reactivated"] == 4
    assert client.get(f"{API}/units/{w['B'][2]['id']}", headers=auth("op.ravi")).json()["eligibility"]["dispatch_eligible"] is True
    assert client.post(f"{API}/recalls/{r['id']}/release", json={"reason": "again"}, headers=auth("qm.farhan")).status_code == 409


def test_quarantine_races_departure_consistently(client, auth, db):
    c, A, _, _ = released_lot(client, auth, db)
    o = _order(client, auth, db, c["part"]["id"], "SO-RACE", 4)
    p = sealed(client, auth, o["id"], [u["id"] for u in A[:4]], seed=70)
    s = _ship(client, auth, o["id"], p, "race-ship")
    h_qm, h_d = auth("qm.farhan"), auth("disp.kiran")

    def recall():
        return client.post(f"{API}/recalls", json={"scope": {"type": "HEAT", "code": "H-7781"}, "reason": "race"}, headers=h_qm)

    def depart():
        return client.post(f"{API}/shipments/{s['id']}/events", json={"type": "DEPARTED", "location": "Gate"}, headers=h_d)
    with cf.ThreadPoolExecutor(2) as ex:
        fr, fd = ex.submit(recall), ex.submit(depart)
        r, d = fr.result(), fd.result()
    assert r.status_code in (201, 409)
    if r.status_code == 409:                      # genealogy moved under the lock: retry is the contract
        r = recall()
    rec = r.json()
    if d.status_code == 200:                      # departure committed first → in-transit action, nothing "blocked at plant"
        assert rec["unit_counts"]["in_transit"] == 4 and {t["type"] for t in rec["segregation_tasks"]} == {"IN_TRANSIT_RECALL"}
    else:                                         # quarantine committed first → departure blocked with a removal task
        assert d.json()["error"]["code"] == "DEPARTURE_BLOCKED"
        assert rec["unit_counts"]["loaded_not_departed"] == 4
        assert db.scalar(select(func.count()).select_from(m.SegregationTask).where(m.SegregationTask.type == "REMOVAL_REQUIRED")) == 1
    assert db.scalar(select(func.count()).select_from(m.Load).where(m.Load.removed_at.is_(None))) == 1
