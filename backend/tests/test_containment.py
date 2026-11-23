"""4M containment runs on real PostgreSQL (CONTRACT §10.1). Images are SYNTHETIC_TEST renders."""
import concurrent.futures as cf

from sqlalchemy import select

from app.db import models as m
from tests.test_inspection_passports import inspect, lot_with_units

API = "/api/v1"


def log_4m(client, auth, line_id, planned=False, who="qe.ananya"):
    return client.post(f"{API}/four-m-events", json={"category": "MACHINE", "planned": planned, "line_id": line_id,
                                                      "description": "Boring insert replaced", "reason": "Chipped mid-shift"}, headers=auth(who))


def run_of(client, auth, rid):
    return client.get(f"{API}/containments/{rid}", headers=auth("op.ravi")).json()


def started(client, auth, db, n=12, pre_pass=3):
    c, units, sample = lot_with_units(client, auth, db, n=n)
    non_sample = [u for u in units if u["id"] not in sample]
    pre = non_sample[:pre_pass]
    for i, u in enumerate(pre):
        assert inspect(client, auth, u["id"], seed=200 + i).json()["passport"]["status"] == "ACTIVE"
    r = log_4m(client, auth, c["line"]["id"])
    assert r.status_code == 201, r.text
    return c, units, sample, pre, r.json()


def test_event_scopes_inventory_holds_passports_and_alerts(client, auth, db):
    c, units, sample, pre, r = started(client, auth, db)
    run = r["containment"]
    assert run["status"] == "ACTIVE" and run["containment_required_qty"] == 5 and run["sequence_epoch"] == 1
    assert run["scope"] == {"existing_recheck": 3, "new_production": 9}
    assert [o["type"] for o in run["obligations"]] == ["FULL_INSPECT_FIRST_N", "RECHECK_UNIT"]
    u = client.get(f"{API}/units/{pre[0]['id']}", headers=auth("op.ravi")).json()
    codes = {b["code"] for b in u["eligibility"]["blockers"]}
    assert {"CONTAINMENT_ACTIVE", "PASSPORT_HELD"} <= codes and u["four_m_tagged"] and u["requires_individual_inspection"]
    assert db.scalars(select(m.Alert).where(m.Alert.type == "FOUR_M_HOLD")).first().audible_siren is True
    assert db.scalars(select(m.OutboxEvent).where(m.OutboxEvent.type == "LOT_4M_CHANGED")).first() is not None
    assert log_4m(client, auth, c["line"]["id"], who="op.ravi").status_code == 403


def test_counter_distinct_pass_hold_zero_reject_resets(client, auth, db):
    c, units, sample, pre, r = started(client, auth, db)
    rid = r["containment"]["id"]
    new = [u for u in units if u["id"] not in {p["id"] for p in pre}]
    a = inspect(client, auth, new[0]["id"], seed=1).json()
    assert a["verdict"] == "PASS" and a["passport"] is None and a["issuance_blockers"][0]["code"] == "CONTAINMENT_ACTIVE"
    assert run_of(client, auth, rid)["containment_cleared_qty"] == 1
    inspect(client, auth, new[0]["id"], seed=2)                       # duplicate PASS on same unit counts once
    assert run_of(client, auth, rid)["containment_cleared_qty"] == 1
    h = inspect(client, auth, new[1]["id"], "blurry").json()          # HOLD earns nothing
    assert h["verdict"] == "HOLD" and run_of(client, auth, rid)["containment_cleared_qty"] == 1
    inspect(client, auth, new[2]["id"], seed=3)
    assert run_of(client, auth, rid)["containment_cleared_qty"] == 2
    rej = inspect(client, auth, new[3]["id"], "defect", seed=4).json()
    assert rej["verdict"] == "REJECT" and rej["containment_progress"][0]["reset"] is True
    after = run_of(client, auth, rid)
    assert after["containment_cleared_qty"] == 0 and after["sequence_epoch"] == 2 and after["status"] == "ACTIVE"
    # evidence from epoch 1 is preserved
    assert db.scalar(select(m.ContainmentCredit).where(m.ContainmentCredit.run_id == rid, m.ContainmentCredit.epoch == 1).limit(1)) is not None


def test_clearance_requires_obligations_then_mints_tagged_passports(client, auth, db):
    c, units, sample, pre, r = started(client, auth, db)
    rid = r["containment"]["id"]
    early = client.post(f"{API}/containments/{rid}/clear", json={"reason": "ok"}, headers=auth("qm.farhan"))
    assert early.status_code == 409 and early.json()["error"]["code"] == "CONTAINMENT_NOT_SATISFIED"
    assert {u["type"] for u in early.json()["error"]["details"]["unmet"]} == {"FULL_INSPECT_FIRST_N", "RECHECK_UNIT"}
    new = [u for u in units if u["id"] not in {p["id"] for p in pre}]
    for i, u in enumerate(pre + new[:2]):
        assert inspect(client, auth, u["id"], seed=300 + i).json()["verdict"] == "PASS"
    assert run_of(client, auth, rid)["status"] == "READY_FOR_CLEARANCE"
    assert client.post(f"{API}/containments/{rid}/clear", json={"reason": "ok"}, headers=auth("qe.ananya")).status_code == 403
    done = client.post(f"{API}/containments/{rid}/clear", json={"reason": "First-off approved, 5 pieces + rechecks PASS"}, headers=auth("qm.farhan")).json()
    assert done["status"] == "CLEARED" and done["cleared_by"] == "qm.farhan" and done["tagged_passports_issued"] == 5
    d = client.get(f"{API}/units/{pre[0]['id']}/detail", headers=auth("op.ravi")).json()
    assert d["passport"]["four_m_tagged"] is True and d["passport"]["inspection_basis"] == "INDIVIDUAL_100PCT"
    assert [h["status"] for h in d["passport_history"]] == ["SUPERSEDED", "ACTIVE"] and d["eligibility"]["dispatch_eligible"] is True
    untouched = client.get(f"{API}/units/{new[5]['id']}", headers=auth("op.ravi")).json()
    assert untouched["passport_id"] is None and untouched["requires_individual_inspection"] is True
    assert client.post(f"{API}/containments/{rid}/clear", json={"reason": "again"}, headers=auth("qm.farhan")).json()["error"]["code"] == "NOT_ACTIVE"


def test_overlapping_events_are_independent_runs(client, auth, db):
    c, units, sample, pre, r1 = started(client, auth, db, pre_pass=0)
    r2 = log_4m(client, auth, c["line"]["id"]).json()
    assert r1["containment"]["id"] != r2["containment"]["id"]
    for i, u in enumerate(units[:5]):
        inspect(client, auth, u["id"], seed=400 + i)
    a, b = run_of(client, auth, r1["containment"]["id"]), run_of(client, auth, r2["containment"]["id"])
    assert a["status"] == b["status"] == "READY_FOR_CLEARANCE"
    client.post(f"{API}/containments/{a['id']}/clear", json={"reason": "run 1 ok"}, headers=auth("qm.farhan"))
    u = client.get(f"{API}/units/{units[0]['id']}", headers=auth("op.ravi")).json()
    assert u["passport_id"] is None and [x["code"] for x in u["eligibility"]["blockers"]].count("CONTAINMENT_ACTIVE") == 1
    client.post(f"{API}/containments/{b['id']}/clear", json={"reason": "run 2 ok"}, headers=auth("qm.farhan"))
    assert client.get(f"{API}/units/{units[0]['id']}", headers=auth("op.ravi")).json()["eligibility"]["dispatch_eligible"] is True


def test_planned_change_does_not_recheck_existing_stock(client, auth, db):
    c, units, sample = lot_with_units(client, auth, db, n=6)
    done = inspect(client, auth, next(u for u in units if u["id"] not in sample)["id"]).json()
    r = log_4m(client, auth, c["line"]["id"], planned=True).json()["containment"]
    assert r["scope"] == {"existing_recheck": 0, "new_production": 5} and done["unit_id"] not in r["affected_unit_ids"]
    assert client.get(f"{API}/units/{done['unit_id']}", headers=auth("op.ravi")).json()["eligibility"]["dispatch_eligible"] is True


def test_new_production_on_contained_line_is_enrolled(client, auth, db):
    c, units, sample, pre, r = started(client, auth, db)
    lot2 = client.post(f"{API}/lots", json={"lot_code": "L-2611-B", "part_id": c["part"]["id"], "line_id": c["line"]["id"], "heat_ids": [c["heat"]["id"]],
                                            "planned_qty": 3}, headers=auth("op.ravi")).json()
    fresh = client.post(f"{API}/lots/{lot2['id']}/units", json={"count": 3}, headers=auth("op.ravi")).json()["items"]
    run = run_of(client, auth, r["containment"]["id"])
    assert {u["id"] for u in fresh} <= set(run["affected_unit_ids"]) and run["scope"]["new_production"] == 12
    assert all(u["requires_individual_inspection"] for u in client.get(f"{API}/units?lot_id={lot2['id']}", headers=auth("op.ravi")).json()["items"])


def test_concurrent_passes_do_not_lose_credits(client, auth, db):
    c, units, sample, pre, r = started(client, auth, db, pre_pass=0)
    h = auth("op.ravi")
    targets = units[:4]

    def go(u):
        from tests.test_inspection_passports import render
        return client.post(f"{API}/inspections", data={"unit_id": u["id"], "evidence_source": "SYNTHETIC_TEST"},
                           files={"image": ("c.jpg", render("good", 500 + units.index(u)), "image/jpeg")}, headers=h).json()["verdict"]
    with cf.ThreadPoolExecutor(4) as ex:
        assert list(ex.map(go, targets)) == ["PASS"] * 4
    assert run_of(client, auth, r["containment"]["id"])["containment_cleared_qty"] == 4
