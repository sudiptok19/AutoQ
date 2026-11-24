"""Control-room summary, SYNTHETIC fixtures, the evidence `access_token` fallback, and the demo seed story."""
from sqlalchemy import func, select

from app.db import models as m
from scripts.seed_demo import Demo

API = "/api/v1"


def test_demo_seed_story_and_dashboard(client, auth, db):
    out = Demo(client, log=lambda _: None).run()
    assert out == {"dock_shipment": "SHP-0004", "ready_package": "PKG-0004", "recall_heat": "H-7781"}
    s = client.get(f"{API}/dashboard/summary", headers=auth("op.ravi")).json()
    assert s["units_by_status"]["REJECT"] == 1 and s["units_by_status"]["HOLD"] == 1
    assert s["active_containments"] == 1 and s["open_shipments"] == 1
    assert s["held_packages"] == 1                                         # PKG-0006 torn seal → SEAL_REVIEW
    assert {a["type"] for a in client.get(f"{API}/alerts", headers=auth("qe.ananya")).json()["items"]} >= {"FOUR_M_HOLD", "SEAL_TAMPER", "SPC_DRIFT"}
    assert 0 < s["first_pass_yield"] < 1 and s["recent_scans"][0]["decision"] == "ACCEPTED"
    # every image the seed stored is declared SYNTHETIC_TEST
    assert db.scalar(select(func.count()).select_from(m.Inspection).where(m.Inspection.evidence_source != "SYNTHETIC_TEST")) == 0
    ship = client.get(f"{API}/shipments/SHP-0004", headers=auth("disp.kiran")).json()
    pkgs = {p["label"]: p for p in client.get(f"{API}/packages?order_id={ship['order_id']}", headers=auth("disp.kiran")).json()["items"]}
    ok = client.post(f"{API}/shipments/{ship['id']}/scans", json={"token": pkgs["PKG-0004"]["token"], "client_event_id": "t-1"}, headers=auth("disp.kiran")).json()
    assert ok["decision"] == "ACCEPTED"
    stale = client.post(f"{API}/shipments/{ship['id']}/scans", json={"token": pkgs["PKG-0005"]["token"], "client_event_id": "t-2"}, headers=auth("disp.kiran")).json()
    assert {r["code"] for r in stale["reasons"]} == {"SEAL_RECHECK_REQUIRED"}


def test_fixtures_are_synthetic_jpegs_behind_auth(client, auth):
    assert client.get(f"{API}/demo/fixtures/part/good").status_code == 401
    r = client.get(f"{API}/demo/fixtures/part/good?seed=5", headers=auth("op.ravi"))
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg" and r.content[:2] == b"\xff\xd8"
    assert r.headers["x-autoq-evidence-source"] == "SYNTHETIC_TEST"
    assert client.get(f"{API}/demo/fixtures/seal/torn", headers=auth("disp.kiran")).status_code == 200
    assert client.get(f"{API}/demo/fixtures/part/rocket", headers=auth("op.ravi")).status_code == 422


def test_access_token_query_is_accepted_for_evidence_only(client, auth):
    tok = auth("op.ravi")["Authorization"][7:]
    sha = client.post(f"{API}/heats", data={"heat_code": "H-1", "supplier_id": client.post(f"{API}/suppliers", json={"code": "S1", "name": "S"}, headers=auth("admin")).json()["id"],
                                            "material_grade": "x"}, files={"certificate": ("c.pdf", b"%PDF-1.4\n%x\n", "application/pdf")}, headers=auth("admin")).json()["certificate_sha256"]
    assert client.get(f"{API}/evidence/{sha}?access_token={tok}").status_code == 200
    assert client.get(f"{API}/evidence/{sha}?access_token=not-a-jwt").status_code == 401
    assert client.get(f"{API}/evidence/{sha}").status_code == 401
    assert client.get(f"{API}/units?access_token={tok}").status_code == 401           # never on other routes
