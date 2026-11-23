"""Packing, seal revisions, PKG v2 identity and seal rechecks on real PostgreSQL. Images are SYNTHETIC_TEST renders."""
import time

import pytest
from sqlalchemy import select

from app.core.auth import load_principal
from app.core.errors import ApiError
from app.db import models as m
from app.features.inspection_passports.passports import trusted_keys
from app.features.packaging import interim_seal, service as pkgsvc
from app.features.passport_crypto.service import verify as astra_verify
from tests.seal_fixtures import render_seal
from tests.test_inspection_passports import inspect, lot_with_units

API = "/api/v1"


def released_lot(client, auth, db, n=10):
    """Lot whose complete passing sample released passports to every unit."""
    c, units, sample = lot_with_units(client, auth, db, n=n)
    for i, uid in enumerate(sample):
        assert inspect(client, auth, uid, seed=600 + i).json()["verdict"] == "PASS"
    cus = client.post(f"{API}/customers", json={"name": "Demo Assembly Plant", "city": "Gurugram"}, headers=auth("admin")).json()
    order = client.post(f"{API}/orders", json={"order_no": "SO-1001", "customer_id": cus["id"], "part_id": c["part"]["id"], "qty_units": 8,
                                              "destination": "Gurugram Gate 4"}, headers=auth("disp.kiran"))
    assert order.status_code == 201, order.text
    return c, units, sample, order.json()


def new_pkg(client, auth, order_id):
    r = client.post(f"{API}/packages", json={"order_id": order_id}, headers=auth("op.ravi"))
    assert r.status_code == 201, r.text
    return r.json()


def add(client, auth, pkg, unit_id=None, token=None):
    return client.post(f"{API}/packages/{pkg}/items", json={"unit_id": unit_id, "token": token}, headers=auth("op.ravi"))


def seal(client, auth, pkg, kind="enroll", seed=0, who="op.ravi"):
    return client.post(f"{API}/packages/{pkg}/seal", data={"evidence_source": "SYNTHETIC_TEST"}, files={"image": ("s.jpg", render_seal(kind, seed), "image/jpeg")}, headers=auth(who))


def check(client, auth, pkg, kind="intact", seed=1, who="disp.kiran"):
    return client.post(f"{API}/packages/{pkg}/seal-checks", data={"evidence_source": "SYNTHETIC_TEST"}, files={"image": ("p.jpg", render_seal(kind, seed), "image/jpeg")}, headers=auth(who))


def test_orders_rbac(client, auth, db):
    _, _, _, order = released_lot(client, auth, db)
    assert order["order_no"] == "SO-1001"
    assert client.post(f"{API}/orders", json={**{k: order[k] for k in ("customer_id", "part_id", "qty_units", "destination")}, "order_no": "SO-2"},
                       headers=auth("op.ravi")).status_code == 403


def test_pack_only_eligible_units_with_history(client, auth, db):
    _, units, sample, order = released_lot(client, auth, db)
    p1, p2 = new_pkg(client, auth, order["id"]), new_pkg(client, auth, order["id"])
    assert p1["label"] != p2["label"] and p1["label"].startswith("PKG-")
    assert add(client, auth, p1["id"], units[0]["id"]).status_code == 200
    dup = add(client, auth, p2["id"], units[0]["id"]).json()
    assert dup["error"]["code"] == "UNIT_NOT_PACKABLE" and dup["error"]["details"]["blockers"][-1]["code"] == "ALREADY_PACKED"
    rej = next(u for u in units if u["id"] not in sample and u["id"] != units[0]["id"])
    inspect(client, auth, rej["id"], "defect", seed=7)
    bad = add(client, auth, p1["id"], rej["id"]).json()
    assert {b["code"] for b in bad["error"]["details"]["blockers"]} >= {"UNIT_REJECTED"}
    out = client.delete(f"{API}/packages/{p1['id']}/items/{units[0]['id']}?reason=wrong+box", headers=auth("op.ravi")).json()
    assert out["unit_ids"] == []
    assert add(client, auth, p2["id"], units[0]["id"]).status_code == 200
    hist = db.scalars(select(m.PackageItem).where(m.PackageItem.unit_id == units[0]["id"]).order_by(m.PackageItem.added_at)).all()
    assert [h.removed_at is not None for h in hist] == [True, False] and hist[0].removal_reason == "wrong box"


def test_pack_by_passport_token(client, auth, db):
    _, units, _, order = released_lot(client, auth, db)
    tok = client.get(f"{API}/units/{units[3]['id']}/detail", headers=auth("op.ravi")).json()["passport"]["token"]
    p = new_pkg(client, auth, order["id"])
    assert add(client, auth, p["id"], token=tok).json()["unit_ids"] == [units[3]["id"]]
    assert add(client, auth, p["id"], token=tok[:-4] + "AAAA").json()["error"]["code"] == "TOKEN_INVALID"


def test_seal_signs_v2_pkg_and_locks_contents(client, auth, db):
    _, units, _, order = released_lot(client, auth, db)
    p = new_pkg(client, auth, order["id"])
    assert seal(client, auth, p["id"]).json()["error"]["code"] == "PACKAGE_EMPTY"
    for u in units[:4]:
        add(client, auth, p["id"], u["id"])
    blur = seal(client, auth, p["id"], "bad_light")
    assert blur.status_code == 422 and blur.json()["error"]["code"] == "SEAL_REFERENCE_UNUSABLE"
    s = seal(client, auth, p["id"]).json()
    assert s["state"] == "SEALED" and s["seal_revision"]["revision_no"] == 1 and s["last_seal_check"]["kind"] == "ENROLL"
    v = astra_verify(s["token"], trusted_keys(db), "PKG", int(time.time()))
    pp_ids = sorted(db.get(m.PackageItem, i.id).passport_id for i in pkgsvc.open_items(db, p["id"]))
    assert v.payload["items"] == pp_ids and v.payload["seal"] == s["seal_ref_sha256"] and v.payload["seal_revision_id"] == s["seal_revision"]["id"]
    assert add(client, auth, p["id"], units[5]["id"]).json()["error"]["code"] == "PACKAGE_NOT_OPEN"
    live = client.post(f"{API}/verify", json={"token": s["token"]}, headers=auth("disp.kiran")).json()
    assert live["signature_valid"] and live["exists"] and live["status"] == "SEALED"


def test_seal_rechecks_intact_suspicious_unverifiable(client, auth, db):
    _, units, _, order = released_lot(client, auth, db)
    p = new_pkg(client, auth, order["id"])
    add(client, auth, p["id"], units[0]["id"])
    seal(client, auth, p["id"])
    assert check(client, auth, p["id"], who="op.ravi").status_code == 403
    ok = check(client, auth, p["id"], "intact", 2).json()
    assert ok["result"] == "INTACT" and ok["metrics"]["inliers"] >= 40 and ok["evidence"]["diff_url"] and ok["synthetic"]
    dark = check(client, auth, p["id"], "bad_light", 3).json()
    assert dark["result"] == "UNVERIFIABLE" and dark["metrics"]["ssim"] is None and dark["evidence"]["aligned_url"] is None
    torn = check(client, auth, p["id"], "torn", 4).json()
    assert torn["result"] == "SUSPICIOUS"
    assert client.get(f"{API}/packages/{p['id']}", headers=auth("op.ravi")).json()["state"] == "SEAL_REVIEW"
    alert = db.scalars(select(m.Alert).where(m.Alert.type == "SEAL_TAMPER")).first()
    assert alert.severity == "CRITICAL" and alert.audible_siren


def test_re_enroll_after_review_issues_new_revision_and_token(client, auth, db):
    _, units, _, order = released_lot(client, auth, db)
    p = new_pkg(client, auth, order["id"])
    add(client, auth, p["id"], units[0]["id"])
    old = seal(client, auth, p["id"]).json()
    assert client.post(f"{API}/packages/{p['id']}/re-enroll", data={"reason": "x"}, files={"image": ("s.jpg", render_seal("enroll"), "image/jpeg")},
                       headers=auth("qe.ananya")).json()["error"]["code"] == "NOT_IN_SEAL_REVIEW"
    check(client, auth, p["id"], "torn", 5)
    new = client.post(f"{API}/packages/{p['id']}/re-enroll", data={"reason": "Inspected contents, applied new seal", "evidence_source": "SYNTHETIC_TEST"},
                      files={"image": ("s.jpg", render_seal("enroll", 9), "image/jpeg")}, headers=auth("qe.ananya")).json()
    assert new["state"] == "SEALED" and new["seal_revision"]["revision_no"] == 2 and new["token"] != old["token"]
    stale = client.post(f"{API}/verify", json={"token": old["token"]}, headers=auth("disp.kiran")).json()
    assert stale["signature_valid"] is True and stale["exists"] is False and stale["status"] == "TOKEN_MISMATCH"


def test_unpack_invalidates_revision_and_token(client, auth, db):
    _, units, _, order = released_lot(client, auth, db)
    p = new_pkg(client, auth, order["id"])
    add(client, auth, p["id"], units[0]["id"])
    seal(client, auth, p["id"])
    u = client.post(f"{API}/packages/{p['id']}/unpack", json={"reason": "customer asked for mixed lot"}, headers=auth("op.ravi")).json()
    assert u["state"] == "OPEN" and u["token"] is None and u["seal_revision"] is None
    assert check(client, auth, p["id"]).json()["error"]["code"] == "SEAL_NOT_ENROLLED"
    assert db.scalars(select(m.SealRevision).where(m.SealRevision.package_id == p["id"])).one().invalidated_at is not None


def test_recheck_against_stale_revision_is_refused(client, auth, db):
    _, units, _, order = released_lot(client, auth, db)
    p = new_pkg(client, auth, order["id"])
    add(client, auth, p["id"], units[0]["id"])
    seal(client, auth, p["id"])
    ctx = pkgsvc.load_seal_context(db, p["id"])                       # analysis starts on revision 1 ...
    ev = interim_seal.compare(ctx.reference_bytes, render_seal("intact", 1), ctx.profile)
    db.rollback()
    client.post(f"{API}/packages/{p['id']}/unpack", json={"reason": "reseal"}, headers=auth("op.ravi"))
    seal(client, auth, p["id"], seed=3)                                # ... meanwhile resealed as revision 2
    with pytest.raises(ApiError) as e:
        pkgsvc.record_recheck(db, ctx, ev, render_seal("intact", 1), "image/jpeg", "SYNTHETIC_TEST", load_principal(db, "usr_kiran"))
    assert e.value.code == "SEAL_REVISION_CHANGED"
    db.rollback()


def test_cannot_seal_if_contents_became_ineligible(client, auth, db):
    c, units, _, order = released_lot(client, auth, db)
    p = new_pkg(client, auth, order["id"])
    add(client, auth, p["id"], units[0]["id"])
    client.post(f"{API}/four-m-events", json={"category": "MATERIAL", "planned": False, "line_id": c["line"]["id"], "description": "Backup heat",
                                              "reason": "Shortage"}, headers=auth("qe.ananya"))
    r = seal(client, auth, p["id"]).json()
    assert r["error"]["code"] == "CONTENTS_NOT_ELIGIBLE"
    assert {b["code"] for b in r["error"]["details"]["units"][0]["blockers"]} >= {"CONTAINMENT_ACTIVE"}
