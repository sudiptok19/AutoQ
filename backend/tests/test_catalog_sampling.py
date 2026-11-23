import concurrent.futures as cf

from sqlalchemy import select

from app.db import models as m

API = "/api/v1"
PDF = b"%PDF-1.4\n% demo mill certificate\n"


def setup_catalog(client, auth, *, a_part=False, planned=48):
    A = auth("admin")
    sup = client.post(f"{API}/suppliers", json={"code": "SUP-014", "name": "Shree Forgings (demo)", "synthetic": True}, headers=A).json()
    part = client.post(f"{API}/parts", json={"part_number": "PN-A" if a_part else "PN-44120", "name": "Bracket", "drawing_rev": "C", "is_a_part": a_part, "default_sample_size": 10}, headers=A).json()
    line = client.post(f"{API}/lines", json={"name": "Line 2", "station_id": "st_l2_vis"}, headers=A).json()
    heat = client.post(f"{API}/heats", data={"heat_code": "H-7781", "supplier_id": sup["id"], "material_grade": "SG 500/7"},
                       files={"certificate": ("MTC.pdf", PDF, "application/pdf")}, headers=auth("qe.ananya"))
    assert heat.status_code == 201, heat.text
    lot = client.post(f"{API}/lots", json={"lot_code": "L-2611-A", "part_id": part["id"], "line_id": line["id"], "heat_ids": [heat.json()["id"]], "planned_qty": planned}, headers=auth("op.ravi"))
    assert lot.status_code == 201, lot.text
    return {"supplier": sup, "part": part, "line": line, "heat": heat.json(), "lot": lot.json()}


def test_master_data_rbac_and_duplicates(client, auth):
    body = {"code": "S1", "name": "X"}
    assert client.post(f"{API}/suppliers", json=body, headers=auth("op.ravi")).json()["error"]["code"] == "ROLE_FORBIDDEN"
    assert client.post(f"{API}/suppliers", json=body, headers=auth("admin")).status_code == 201
    r = client.post(f"{API}/suppliers", json=body, headers=auth("admin"))
    assert r.status_code == 409 and r.json()["error"]["code"] == "SUPPLIER_EXISTS"
    assert client.get(f"{API}/suppliers", headers=auth("disp.kiran")).json()["items"][0]["code"] == "S1"
    assert client.post(f"{API}/suppliers", json={"code": "bad code!", "name": "X"}, headers=auth("admin")).status_code == 422


def test_heat_certificate_is_evidence_and_media_checked(client, auth):
    c = setup_catalog(client, auth)
    sha = c["heat"]["certificate_sha256"]
    r = client.get(f"{API}/evidence/{sha}", headers=auth("disp.kiran"))
    assert r.status_code == 200 and r.content == PDF
    bad = client.post(f"{API}/heats", data={"heat_code": "H-1", "supplier_id": c["supplier"]["id"], "material_grade": "x"},
                      files={"certificate": ("x.pdf", b"not a pdf", "application/pdf")}, headers=auth("admin"))
    assert bad.status_code == 415 and bad.json()["error"]["code"] == "INVALID_MEDIA"


def test_lot_requires_single_unquarantined_heat(client, auth, db):
    c = setup_catalog(client, auth)
    body = {"lot_code": "L-2", "part_id": c["part"]["id"], "line_id": c["line"]["id"], "heat_ids": [c["heat"]["id"], "heat_x"], "planned_qty": 5}
    assert client.post(f"{API}/lots", json=body, headers=auth("admin")).json()["error"]["code"] == "LOT_SINGLE_HEAT_REQUIRED"
    db.get(m.Heat, c["heat"]["id"]).status = "QUARANTINED"; db.commit()
    body["heat_ids"] = [c["heat"]["id"]]
    assert client.post(f"{API}/lots", json=body, headers=auth("admin")).json()["error"]["code"] == "HEAT_QUARANTINED"


def test_serialize_units_and_planned_limit(client, auth):
    c = setup_catalog(client, auth, planned=12)
    lot = c["lot"]["id"]
    r = client.post(f"{API}/lots/{lot}/units", json={"count": 10}, headers=auth("op.ravi")).json()["items"]
    assert [u["serial"] for u in r[:2]] == ["L-2611-A-0001", "L-2611-A-0002"] and r[0]["production_seq"] == 1
    assert r[0]["eligibility"] == {"dispatch_eligible": False, "blockers": [{"code": "NO_PASSPORT", "message": "No quality passport issued"}]}
    over = client.post(f"{API}/lots/{lot}/units", json={"count": 3}, headers=auth("op.ravi"))
    assert over.status_code == 409 and over.json()["error"]["details"] == {"produced": 10, "planned": 12, "request_id": over.json()["error"]["details"]["request_id"]}
    assert client.get(f"{API}/units/L-2611-A-0002", headers=auth("disp.kiran")).json()["production_seq"] == 2


def test_concurrent_serialization_has_no_duplicate_sequence(client, auth, db):
    c = setup_catalog(client, auth, planned=40)
    h = auth("op.ravi")

    def go(_):
        return client.post(f"{API}/lots/{c['lot']['id']}/units", json={"count": 10}, headers=h).status_code
    with cf.ThreadPoolExecutor(4) as ex:
        assert sorted(ex.map(go, range(4))) == [201] * 4
    seqs = sorted(db.scalars(select(m.Unit.production_seq).where(m.Unit.lot_id == c["lot"]["id"])))
    assert seqs == list(range(1, 41))


def test_freeze_and_sample_systematic_idempotent_and_frozen(client, auth):
    c = setup_catalog(client, auth)
    lot = c["lot"]["id"]
    assert client.post(f"{API}/lots/{lot}/freeze-and-sample", headers=auth("qe.ananya")).json()["error"]["code"] == "LOT_EMPTY"
    units = client.post(f"{API}/lots/{lot}/units", json={"count": 48}, headers=auth("op.ravi")).json()["items"]
    assert client.post(f"{API}/lots/{lot}/freeze-and-sample", headers=auth("op.ravi")).status_code == 403
    f = client.post(f"{API}/lots/{lot}/freeze-and-sample", headers=auth("qe.ananya")).json()
    assert f["policy_version"] == "demo-1" and f["inspection_basis"] == "SAMPLED_LOT" and f["replay"] is False
    seq = {u["id"]: u["production_seq"] for u in units}
    picked = sorted(seq[i] for i in f["sample_unit_ids"])
    assert picked == [1, 13, 25, 36, 48]          # policy sample_size 5 < part default 10; first & last always included
    again = client.post(f"{API}/lots/{lot}/freeze-and-sample", headers=auth("qm.farhan")).json()
    assert again["replay"] is True and sorted(again["sample_unit_ids"]) == sorted(f["sample_unit_ids"])
    late = client.post(f"{API}/lots/{lot}/units", json={"count": 1}, headers=auth("op.ravi"))
    assert late.status_code == 409 and late.json()["error"]["code"] == "LOT_FROZEN"
    lv = client.get(f"{API}/lots/{lot}", headers=auth("op.ravi")).json()
    assert lv["sample_status"] == "IN_PROGRESS" and lv["counts"]["UNINSPECTED"] == 48 and lv["frozen_at"]
    assert client.get(f"{API}/units/{f['sample_unit_ids'][0]}", headers=auth("op.ravi")).json()["sampled"] is True


def test_a_part_lot_has_no_sample_and_requires_individual(client, auth):
    c = setup_catalog(client, auth, a_part=True, planned=6)
    lot = c["lot"]["id"]
    us = client.post(f"{API}/lots/{lot}/units", json={"count": 6}, headers=auth("op.ravi")).json()["items"]
    assert all(u["requires_individual_inspection"] for u in us)
    f = client.post(f"{API}/lots/{lot}/freeze-and-sample", headers=auth("qe.ananya")).json()
    assert f["inspection_basis"] == "INDIVIDUAL_100PCT" and f["sample_unit_ids"] == []


def test_recall_hold_on_heat_marks_units_quarantined(client, auth, db):
    c = setup_catalog(client, auth, planned=2)
    client.post(f"{API}/lots/{c['lot']['id']}/units", json={"count": 2}, headers=auth("op.ravi"))
    db.add(m.Recall(id="rcl_t", scope_type="HEAT", scope_id=c["heat"]["id"], reason="t", impact={}, actor_id="usr_farhan"))
    db.flush()
    db.add(m.RecallHold(id="rh_t", recall_id="rcl_t", entity_type="HEAT", entity_id=c["heat"]["id"])); db.commit()
    u = client.get(f"{API}/units/L-2611-A-0001", headers=auth("op.ravi")).json()
    assert u["held"] is True and "QUARANTINED" in [b["code"] for b in u["eligibility"]["blockers"]]
    assert client.get(f"{API}/lots/{c['lot']['id']}", headers=auth("op.ravi")).json()["held"] is True


def test_policy_versions(client, auth):
    assert client.get(f"{API}/policies", headers=auth("op.ravi")).json()["version"] == "demo-1"
    bad = client.put(f"{API}/policies", json={"inspection": {"hold_threshold": 0.8, "reject_threshold": 0.7}}, headers=auth("admin"))
    assert bad.status_code == 422
    assert client.put(f"{API}/policies", json={"sampling": {"sample_size": 3}}, headers=auth("qm.farhan")).status_code == 403
    p = client.put(f"{API}/policies", json={"version": "p2", "sampling": {"sample_size": 3}}, headers=auth("admin")).json()
    assert p["version"] == "p2" and p["settings"]["sampling"]["sample_size"] == 3
    assert client.get(f"{API}/policies", headers=auth("op.ravi")).json()["version"] == "p2"
