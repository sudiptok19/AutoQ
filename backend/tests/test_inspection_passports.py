"""Inspection → verdict → passport lifecycle on real PostgreSQL. Images are SYNTHETIC_TEST renders."""
import cv2
import numpy as np
from sqlalchemy import select

from app.core.storage import put_evidence
from app.db import models as m
from app.features.inspection_passports import interim_vision
from app.features.passport_crypto.service import verify as astra_verify
from app.features.inspection_passports.passports import trusted_keys
from tests.test_catalog_sampling import setup_catalog

API = "/api/v1"


def render(kind: str = "good", seed: int = 1) -> bytes:
    rng = np.random.default_rng(seed)
    img = np.full((480, 640, 3), 60, np.uint8)
    shift = (0, 0) if kind == "golden" else tuple(int(x) for x in rng.integers(-3, 4, 2))
    pts = np.array([[130, 120], [470, 110], [522, 160], [528, 330], [480, 374], [300, 380], [250, 300], [140, 300], [110, 262]], np.int32) + shift
    cv2.fillPoly(img, [pts], (175, 180, 186))
    cv2.polylines(img, [pts], True, (90, 95, 100), 3)
    for (x, y, r) in [(175, 210, 30), (440, 175, 26), (450, 315, 26)]:
        cv2.circle(img, (x + shift[0], y + shift[1]), r, (30, 32, 35), -1)
    if kind == "defect":
        cv2.line(img, (205 + shift[0], 222 + shift[1]), (281 + shift[0], 250 + shift[1]), (20, 20, 20), 5)
        cv2.fillPoly(img, [np.array([[470, 110], [500, 118], [490, 142]], np.int32) + shift], (60, 60, 60))
    if kind == "blurry":
        img = cv2.GaussianBlur(img, (31, 31), 12)
    if kind == "glare":
        cv2.circle(img, (330, 220), 200, (255, 255, 255), -1)
    noise = rng.normal(0, 3, img.shape)
    img = np.clip(img.astype(float) + noise, 0, 255).astype(np.uint8)
    return cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 92])[1].tobytes()


def calibrate(db, part_id: str, station_id: str = "st_l2_vis") -> str:
    sha = put_evidence(db, render("golden"), "image/jpeg", "SYNTHETIC_TEST", None)
    prof = m.InspectionProfile(id=f"prof_{part_id[-6:]}", part_id=part_id, station_id=station_id, method_version=interim_vision.METHOD_VERSION,
                               calibration_version=1, calibrated=True, reference_sha256=sha, thresholds={})
    db.add(prof); db.commit()
    return prof.id


def lot_with_units(client, auth, db, n=10, *, a_part=False, freeze=True, calibrated=True):
    c = setup_catalog(client, auth, a_part=a_part, planned=n)
    units = client.post(f"{API}/lots/{c['lot']['id']}/units", json={"count": n}, headers=auth("op.ravi")).json()["items"]
    if calibrated:
        calibrate(db, c["part"]["id"])
    sample = []
    if freeze:
        sample = client.post(f"{API}/lots/{c['lot']['id']}/freeze-and-sample", headers=auth("qe.ananya")).json()["sample_unit_ids"]
    return c, units, sample


def inspect(client, auth, unit_id, kind="good", seed=1, who="op.ravi"):
    return client.post(f"{API}/inspections", data={"unit_id": unit_id, "evidence_source": "SYNTHETIC_TEST"},
                       files={"image": ("cap.jpg", render(kind, seed), "image/jpeg")}, headers=auth(who))


def test_unfrozen_lot_and_uncalibrated_profile_fail_safe(client, auth, db):
    c, units, _ = lot_with_units(client, auth, db, n=4, freeze=False, calibrated=False)
    r = inspect(client, auth, units[0]["id"])
    assert r.status_code == 409 and r.json()["error"]["code"] == "LOT_NOT_FROZEN"
    client.post(f"{API}/lots/{c['lot']['id']}/freeze-and-sample", headers=auth("qe.ananya"))
    r = inspect(client, auth, units[0]["id"]).json()
    assert r["verdict"] == "HOLD" and r["analysis"]["score"] is None and r["passport"] is None
    assert [x["code"] for x in r["reasons"]] == ["MODEL_UNCALIBRATED"]


def test_good_part_passes_and_gets_signed_v2_individual_passport(client, auth, db):
    _, units, sample = lot_with_units(client, auth, db)
    unsampled = next(u for u in units if u["id"] not in sample)
    r = inspect(client, auth, unsampled["id"]).json()
    assert r["verdict"] == "PASS", r
    assert r["analysis"]["score"] < 0.45 and r["evidence"]["heatmap_url"] and r["synthetic"] is True
    pp = r["passport"]
    assert pp["inspection_basis"] == "INDIVIDUAL_100PCT" and pp["authorizing_inspection_ids"] == [r["id"]] and pp["status"] == "ACTIVE"
    v = astra_verify(pp["token"], trusted_keys(db), "PP", int(__import__("time").time()))
    assert v.v == 2 and v.payload["serial"] == unsampled["serial"] and v.payload["heats"] == ["H-7781"]
    live = client.post(f"{API}/verify", json={"token": pp["token"]}, headers=auth("disp.kiran")).json()
    assert live["signature_valid"] and live["exists"] and live["status"] == "ACTIVE" and live["eligibility"]["dispatch_eligible"] is True
    img = client.get(r["evidence"]["image_url"], headers=auth("disp.kiran"))
    assert img.status_code == 200 and img.headers["content-type"] == "image/jpeg"


def test_forged_token_is_rejected_by_verify(client, auth, db):
    _, units, sample = lot_with_units(client, auth, db)
    tok = inspect(client, auth, next(u for u in units if u["id"] not in sample)["id"]).json()["passport"]["token"]
    h, p, s = tok.split(".")
    forged = f"{h}.{p[:-2]}{'A' if p[-2] != 'A' else 'B'}{p[-1]}.{s}"
    r = client.post(f"{API}/verify", json={"token": forged}, headers=auth("disp.kiran")).json()
    assert r["signature_valid"] is False and r["failure"] in {"SIG_INVALID", "TOKEN_MALFORMED", "SCHEMA_INVALID"}


def test_blur_holds_then_review_gates_reinspection(client, auth, db):
    _, units, _ = lot_with_units(client, auth, db)
    u = units[1]["id"]
    r = inspect(client, auth, u, "blurry").json()
    assert r["verdict"] == "HOLD" and "IMAGE_QUALITY_BLUR" in [x["code"] for x in r["reasons"]] and r["analysis"]["score"] is None
    blocked = inspect(client, auth, u)
    assert blocked.status_code == 409 and blocked.json()["error"]["code"] == "HOLD_REVIEW_REQUIRED"
    assert client.post(f"{API}/inspections/{r['id']}/review", json={"action": "REINSPECT", "reason": "x"}, headers=auth("op.ravi")).status_code == 403
    ok = client.post(f"{API}/inspections/{r['id']}/review", json={"action": "REINSPECT", "reason": "recapture under diffuser"}, headers=auth("qe.ananya"))
    assert ok.status_code == 200 and ok.json()["review"]["by"] == "qe.ananya"
    assert inspect(client, auth, u).json()["verdict"] == "PASS"


def test_glare_is_hold(client, auth, db):
    _, units, _ = lot_with_units(client, auth, db)
    assert "IMAGE_QUALITY_GLARE" in [x["code"] for x in inspect(client, auth, units[2]["id"], "glare").json()["reasons"]]


def test_defect_rejects_and_revokes_existing_passport(client, auth, db):
    _, units, sample = lot_with_units(client, auth, db)
    u = next(x for x in units if x["id"] not in sample)["id"]
    first = inspect(client, auth, u).json()
    assert first["passport"]["status"] == "ACTIVE"
    r = inspect(client, auth, u, "defect", seed=2).json()
    assert r["verdict"] == "REJECT" and r["analysis"]["score"] >= 0.70 and r["analysis"]["regions"]
    assert db.get(m.Passport, first["passport"]["id"]).status == "REVOKED"
    assert db.scalars(select(m.OutboxEvent).where(m.OutboxEvent.type == "PASSPORT_REVOKED")).first() is not None
    assert inspect(client, auth, u).json()["error"]["code"] == "UNIT_REJECTED"


def test_complete_sample_releases_sampled_passports_and_individual_supersedes(client, auth, db):
    c, units, sample = lot_with_units(client, auth, db)
    assert len(sample) == 5
    for i, uid in enumerate(sample):
        r = inspect(client, auth, uid, seed=10 + i).json()
        assert r["verdict"] == "PASS"
    assert r["lot_sample_status"] == "PASSED" and r["sampled_passports_released"] == 5
    other = next(u for u in units if u["id"] not in sample)
    d = client.get(f"{API}/units/{other['id']}/detail", headers=auth("op.ravi")).json()
    pp = d["passport"]
    assert pp["inspection_basis"] == "SAMPLED_LOT" and d["quality_status"] == "UNINSPECTED" and d["eligibility"]["dispatch_eligible"] is True
    sample_insp = {i["id"] for uid in sample for i in client.get(f"{API}/inspections?unit_id={uid}", headers=auth("op.ravi")).json()["items"]}
    assert set(pp["authorizing_inspection_ids"]) == sample_insp and len(pp["evidence_digests"]) == 5
    again = client.post(f"{API}/passports/issue", json={"unit_id": other["id"]}, headers=auth("op.ravi")).json()
    assert again["id"] == pp["id"]  # idempotent
    ind = inspect(client, auth, other["id"], seed=99).json()["passport"]
    assert ind["inspection_basis"] == "INDIVIDUAL_100PCT" and ind["id"] != pp["id"]
    old = client.get(f"{API}/passports/{pp['id']}", headers=auth("op.ravi")).json()
    assert old["status"] == "SUPERSEDED" and old["superseded_by_passport_id"] == ind["id"]
    hist = client.get(f"{API}/units/{other['id']}/detail", headers=auth("op.ravi")).json()["passport_history"]
    assert [h["status"] for h in hist] == ["SUPERSEDED", "ACTIVE"]


def test_failed_sample_holds_release_and_alerts(client, auth, db):
    c, units, sample = lot_with_units(client, auth, db)
    r = inspect(client, auth, sample[0], "defect", seed=3).json()
    assert r["verdict"] == "REJECT" and r["lot_sample_status"] == "FAILED"
    other = next(u for u in units if u["id"] not in sample)
    res = client.post(f"{API}/passports/issue", json={"unit_id": other["id"]}, headers=auth("op.ravi"))
    assert res.status_code == 409 and res.json()["error"]["code"] == "SAMPLE_INCOMPLETE"
    assert db.scalars(select(m.Alert).where(m.Alert.type == "SAMPLE_FAILED")).first().severity == "HIGH"


def test_a_part_requires_individual_pass(client, auth, db):
    _, units, sample = lot_with_units(client, auth, db, n=3, a_part=True)
    assert sample == []
    r = client.post(f"{API}/passports/issue", json={"unit_id": units[0]["id"]}, headers=auth("op.ravi"))
    assert r.json()["error"]["code"] == "INDIVIDUAL_INSPECTION_REQUIRED"
    assert inspect(client, auth, units[0]["id"]).json()["passport"]["inspection_basis"] == "INDIVIDUAL_100PCT"


def test_red_containment_blocks_minting_but_keeps_evidence(client, auth, db):
    c, units, sample = lot_with_units(client, auth, db)
    u = next(x for x in units if x["id"] not in sample)
    ev = m.FourMEvent(id="fm_t", code="FM-T", line_id=c["line"]["id"], category="MACHINE", planned=False, description="insert", reason="chip",
                      effective_at=db.get(m.Lot, c["lot"]["id"]).created_at, policy_version="demo-1", actor_id="usr_ananya")
    db.add(ev); db.flush()
    db.add(m.ContainmentRun(id="run_t", event_id="fm_t", line_id=c["line"]["id"], required_qty=5, rule={}, policy_version="demo-1")); db.flush()
    db.add(m.ContainmentUnit(run_id="run_t", unit_id=u["id"], production_seq=u["production_seq"], scope_role="NEW_PRODUCTION")); db.commit()
    r = inspect(client, auth, u["id"]).json()
    assert r["verdict"] == "PASS" and r["passport"] is None and r["contained"] is True
    assert [b["code"] for b in r["issuance_blockers"]] == ["CONTAINMENT_ACTIVE"]
    assert client.post(f"{API}/passports/issue", json={"unit_id": u["id"]}, headers=auth("qm.farhan")).json()["error"]["code"] == "CONTAINMENT_ACTIVE"


def test_astra_routes_are_wired(client, auth, db):
    _, units, sample = lot_with_units(client, auth, db)
    inspect(client, auth, next(u for u in units if u["id"] not in sample)["id"])
    keys = client.get(f"{API}/keys").json()["keys"]
    assert keys and keys[0]["kid"] == "aq-test-1" and keys[0]["status"] == "ACTIVE"
    part = db.scalars(select(m.Part)).first()
    s = client.get(f"{API}/spc/series", params={"part_id": part.id, "line_id": db.scalars(select(m.Line)).first().id,
                                                 "method_version": interim_vision.METHOD_VERSION}, headers=auth("op.ravi")).json()
    assert s["baseline"]["status"] == "COLLECTING" and len(s["points"]) == 1 and s["points"][0]["z"] is None and s["points"][0]["synthetic"] is True
    pdf = client.get(f"{API}/reports/rpt_x/pdf", headers=auth("op.ravi"))
    assert pdf.status_code == 503 and pdf.json()["error"]["code"] == "ADAPTER_UNAVAILABLE"
