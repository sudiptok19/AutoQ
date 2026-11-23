"""Inspection profile lifecycle: create → golden (Astra A1 route) → calibrate (new immutable revision) → inspect."""
import json

from tests.test_inspection_passports import inspect, render
from tests.test_catalog_sampling import setup_catalog

API = "/api/v1"
EVAL = {"method_version": "gd-0.3", "samples": 40, "false_accepts": 0, "false_rejects": 1, "unverifiable": 3, "evidence_source": "PRERECORDED_EVAL"}


def test_profile_create_golden_calibrate_inspect(client, auth, db):
    c = setup_catalog(client, auth, planned=4)
    units = client.post(f"{API}/lots/{c['lot']['id']}/units", json={"count": 4}, headers=auth("op.ravi")).json()["items"]
    client.post(f"{API}/lots/{c['lot']['id']}/freeze-and-sample", headers=auth("qe.ananya"))
    prof = client.post(f"{API}/inspection-profiles", json={"part_id": c["part"]["id"], "station_id": "st_l2_vis"}, headers=auth("qe.ananya")).json()
    assert prof["calibrated"] is False and prof["method_version"] == "gd-0.3"
    assert client.post(f"{API}/inspection-profiles", json={"part_id": c["part"]["id"], "station_id": "st_l2_vis"}, headers=auth("qe.ananya")).status_code == 409
    no_ref = client.post(f"{API}/inspection-profiles/{prof['id']}/calibrate", data={"hold": 0.45, "reject": 0.7, "reason": "x"},
                         files={"evaluation": ("e.json", json.dumps(EVAL).encode(), "application/json")}, headers=auth("qm.farhan"))
    assert no_ref.json()["error"]["code"] == "REFERENCE_MISSING"
    g = client.post(f"{API}/inspection-profiles/{prof['id']}/golden", files={"image": ("g.jpg", render("golden"), "image/jpeg")}, headers=auth("qe.ananya"))
    assert g.status_code == 200, g.text
    rev2 = g.json()
    assert rev2["calibrated"] is False and rev2["calibration_version"] == 2
    assert inspect(client, auth, units[0]["id"]).json()["reasons"][0]["code"] == "MODEL_UNCALIBRATED"
    assert client.post(f"{API}/inspection-profiles/{rev2['id']}/calibrate", data={"hold": 0.45, "reject": 0.7, "reason": "x"},
                       files={"evaluation": ("e.json", json.dumps(EVAL).encode(), "application/json")}, headers=auth("qe.ananya")).status_code == 403
    bad = client.post(f"{API}/inspection-profiles/{rev2['id']}/calibrate", data={"hold": 0.45, "reject": 0.7, "reason": "x"},
                      files={"evaluation": ("e.json", b'{"samples": "many"}', "application/json")}, headers=auth("qm.farhan"))
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "EVALUATION_INVALID"
    cal = client.post(f"{API}/inspection-profiles/{rev2['id']}/calibrate", data={"hold": 0.45, "reject": 0.7, "reason": "Held-out tabletop session 2"},
                      files={"evaluation": ("e.json", json.dumps(EVAL).encode(), "application/json")}, headers=auth("qm.farhan")).json()
    assert cal["calibrated"] is True and cal["calibration_version"] == 3 and cal["thresholds"]["calibration"]["false_rejects"] == 1
    assert client.get(f"{API}/inspection-profiles/{rev2['id']}", headers=auth("op.ravi")).json()["active"] is False
    assert client.get(f"{API}/evidence/{cal['thresholds']['calibration']['evaluation_sha256']}", headers=auth("op.ravi")).status_code == 200
    r = inspect(client, auth, units[1]["id"]).json()
    assert r["verdict"] == "PASS" and r["profile_id"] == cal["id"] and r["analysis"]["calibrated"] is True and r["analysis"]["method_version"] == "gd-0.3"
