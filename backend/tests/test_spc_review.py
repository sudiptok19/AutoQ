"""A3 integration: Astra's pure investigation + recommendation, Opus storage, human approval, quarantine."""
import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app.db import models as m
from app.features.console_demo.synthetic import render
from tests.test_inspection_passports import lot_with_units

API = "/api/v1"


def live_inspect(client, auth, unit_id, seed, wear=0.0):
    r = client.post(f"{API}/inspections", data={"unit_id": unit_id}, files={"image": ("cap.jpg", render("good", seed, wear=wear), "image/jpeg")},
                    headers=auth("op.ravi"))
    assert r.status_code in (200, 201), r.text
    return r.json()


@pytest.fixture
def drifting_lot(client, auth, db):
    """40 units inspected as LIVE captures: 30 stable parts, then a rising tool-wear trend."""
    c, units, _ = lot_with_units(client, auth, db, n=40)
    out = []
    for i, u in enumerate(units):
        wear = 0.0 if i < 30 else 0.30 + 0.05 * (i - 30)
        out.append(live_inspect(client, auth, u["id"], seed=2000 + i, wear=wear))
    return c, units, out


def review(client, auth, inspection_id, who="qe.ananya"):
    return client.post(f"{API}/spc-reviews", json={"inspection_id": inspection_id}, headers=auth(who))


def test_a_live_drift_yields_a_reviewable_scope_that_only_a_manager_can_turn_into_holds(client, auth, db, drifting_lot):
    c, units, insps = drifting_lot
    r = review(client, auth, insps[-1]["id"])
    assert r.status_code == 201, r.text
    rv = r.json()
    assert rv["investigation_status"] == "SIGNAL" and rv["recommendation_status"] == "REVIEW_RECOMMENDED", rv["issue_counts"]
    lot_id = c["lot"]["id"]
    proposed = {x["lot_id"]: x for x in rv["recommendation"]["proposed_lots"]}
    assert lot_id in proposed and "SIGNAL_CONTRIBUTOR" in proposed[lot_id]["basis"]
    assert set(proposed[lot_id]["recorded_unit_ids"]) == {u["id"] for u in units}              # whole recorded roster, for review
    assert rv["recommendation"]["confirmed_observations"] and rv["read_context"]["exposure_links_read"] == 0
    assert review(client, auth, insps[-1]["id"], who="op.ravi").status_code == 403

    rid = rv["id"]
    url = f"{API}/spc-reviews/{rid}"
    body = {"decision": "APPROVE", "reason": "Wear trend confirmed on the OP-20 insert; review the whole lot", "lot_ids": [lot_id]}
    assert client.post(f"{url}/decision", json=body, headers=auth("qe.ananya")).status_code == 403   # approval is a manager's
    assert client.post(f"{url}/decision", json=body | {"lot_ids": ["lot_not_proposed"]}, headers=auth("qm.farhan")).status_code == 422
    assert client.post(f"{url}/quarantine", json={"lot_id": lot_id}, headers=auth("qm.farhan")).status_code == 409  # not approved yet
    ok = client.post(f"{url}/decision", json=body, headers=auth("qm.farhan")).json()
    assert ok["status"] == "APPROVED" and ok["approved_lot_ids"] == [lot_id] and ok["decided_by"] == "qm.farhan"
    assert client.post(f"{url}/decision", json=body, headers=auth("qm.farhan")).json()["error"]["code"] == "SPC_REVIEW_DECIDED"

    q = client.post(f"{url}/quarantine", json={"lot_id": lot_id}, headers=auth("qm.farhan")).json()
    recall_id = q["recall_ids"][lot_id]
    rc = db.get(m.Recall, recall_id)
    assert rc.scope_type == "LOT" and rc.scope_id == lot_id and rid in rc.reason
    assert client.post(f"{url}/quarantine", json={"lot_id": lot_id}, headers=auth("qm.farhan")).json()["error"]["code"] == "ALREADY_QUARANTINED"
    assert db.scalars(select(m.AuditEvent).where(m.AuditEvent.action == "SPC_REVIEW_APPROVED")).first().reason.startswith("Wear trend")


def test_synthetic_evidence_never_supports_containment(client, auth, db):
    """The demo's drift is SYNTHETIC_TEST: it is excluded as non-production, and nothing can be approved."""
    c, units, _ = lot_with_units(client, auth, db, n=4)
    r = client.post(f"{API}/inspections", data={"unit_id": units[0]["id"], "evidence_source": "SYNTHETIC_TEST"},
                    files={"image": ("cap.jpg", render("good", 1), "image/jpeg")}, headers=auth("op.ravi")).json()
    rv = review(client, auth, r["id"]).json()
    assert rv["investigation_status"] == "INSUFFICIENT_EVIDENCE" and rv["issue_counts"].get("NON_PRODUCTION_SOURCE") == 1
    d = client.post(f"{API}/spc-reviews/{rv['id']}/decision", json={"decision": "APPROVE", "reason": "trying to approve anyway", "lot_ids": [c["lot"]["id"]]},
                    headers=auth("qm.farhan"))
    assert d.status_code == 409 and d.json()["error"]["code"] == "NOTHING_TO_APPROVE"
    rej = client.post(f"{API}/spc-reviews/{rv['id']}/decision", json={"decision": "REJECT", "reason": "Synthetic demo data: no action"},
                      headers=auth("qm.farhan")).json()
    assert rej["status"] == "REJECTED"


def test_a_review_freezes_its_evidence(client, auth, db, drifting_lot):
    _, _, insps = drifting_lot
    rid = review(client, auth, insps[-1]["id"]).json()["id"]
    for stmt in ("UPDATE spc_reviews SET recommendation = '{}'::jsonb", "UPDATE spc_reviews SET read_context = '{}'::jsonb", "DELETE FROM spc_reviews"):
        with pytest.raises(IntegrityError):
            db.execute(text(stmt)); db.commit()
        db.rollback()
    assert db.get(m.SpcReview, rid).recommendation["status"] == "REVIEW_RECOMMENDED"


def test_reviews_are_listed_with_their_inputs(client, auth, db, drifting_lot):
    _, _, insps = drifting_lot
    review(client, auth, insps[-1]["id"])
    items = client.get(f"{API}/spc-reviews", headers=auth("op.ravi")).json()["items"]
    assert len(items) == 1 and items[0]["read_context"]["events_read"] == 40 and items[0]["qualification_version"] == "spc-investigation-1"
