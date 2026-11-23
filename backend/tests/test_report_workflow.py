"""8D drafts as frozen snapshots, Astra's A5 PDF via the Ports seam, and QM approval. Real PostgreSQL."""
import json

from app.core.storage import put_evidence
from tests.test_containment import started
from tests.test_inspection_passports import inspect

API = "/api/v1"


def four_m_report(client, auth, db):
    c, units, sample, pre, r = started(client, auth, db)
    new = [u for u in units if u["id"] not in {p["id"] for p in pre}]
    inspect(client, auth, new[0]["id"], seed=1)
    rej = inspect(client, auth, new[1]["id"], "defect", seed=2).json()
    rep = client.post(f"{API}/reports/8d", json={"source": {"type": "FOUR_M", "id": r["event"]["code"]}}, headers=auth("qe.ananya"))
    assert rep.status_code == 201, rep.text
    return rep.json(), rej, units


def test_draft_is_evidence_linked_and_marks_pending(client, auth, db):
    rep, rej, _ = four_m_report(client, auth, db)
    assert rep["status"] == "DRAFT" and rep["generator"].startswith("template") and [s["key"] for s in rep["sections"]] == [f"D{i}" for i in range(1, 9)]
    kinds = {s["key"]: [i["kind"] for i in s["items"]] for s in rep["sections"]}
    assert "HYPOTHESIS" in kinds["D4"] and "PENDING_HUMAN" in kinds["D4"] and "FACT" not in kinds["D4"]
    assert kinds["D6"] == ["PENDING_HUMAN"] and kinds["D8"] == ["PENDING_HUMAN"]
    assert rep["counts"] == {"affected_units": 12, "held_packages": 0, "at_plant": 12, "loaded_not_departed": 0, "in_transit": 0, "delivered": 0}
    d2 = [e for i in rep["sections"][1]["items"] for e in i["evidence"]]
    assert any(e["id"] == rej["id"] and e["sha256"] == rej["evidence"]["image_sha256"] for e in d2)
    assert rep["evidence_source"] == "SYNTHETIC_TEST" and len(rep["snapshot_sha256"]) == 64


def test_snapshot_is_frozen_and_pdf_deterministic(client, auth, db):
    rep, _, units = four_m_report(client, auth, db)
    inspect(client, auth, units[-1]["id"], seed=50)        # later facts must not change the frozen revision
    again = client.get(f"{API}/reports/{rep['id']}", headers=auth("op.ravi")).json()
    assert again["snapshot_sha256"] == rep["snapshot_sha256"] and again["counts"] == rep["counts"]
    a = client.get(f"{API}/reports/{rep['id']}/pdf", headers=auth("op.ravi"))
    b = client.get(f"{API}/reports/{rep['id']}/pdf", headers=auth("disp.kiran"))
    assert a.status_code == 200 and a.headers["content-type"] == "application/pdf" and a.content.startswith(b"%PDF") and a.content == b.content
    sha = client.get(f"{API}/reports/{rep['id']}", headers=auth("op.ravi")).json()["pdf_sha256"]
    assert client.get(f"{API}/evidence/{sha}", headers=auth("op.ravi")).content == a.content


def test_approval_needs_qm_and_review_evidence(client, auth, db):
    rep, _, _ = four_m_report(client, auth, db)
    assert client.post(f"{API}/reports/{rep['id']}/approve", json={"reason": "ok", "review_evidence_ids": []}, headers=auth("qe.ananya")).status_code == 403
    r = client.post(f"{API}/reports/{rep['id']}/approve", json={"reason": "ok", "review_evidence_ids": []}, headers=auth("qm.farhan"))
    assert r.status_code == 409 and r.json()["error"]["code"] == "REPORT_INCOMPLETE"
    sha = put_evidence(db, b"%PDF-1.4\n% review minutes\n", "application/pdf", "UPLOAD", "usr_farhan"); db.commit()
    ok = client.post(f"{API}/reports/{rep['id']}/approve", json={"reason": "Root cause confirmed in review", "review_evidence_ids": [sha]}, headers=auth("qm.farhan")).json()
    assert ok["status"] == "APPROVED" and ok["approved_by"] == "qm.farhan" and ok["revisions"] == 2 and ok["snapshot_sha256"] != rep["snapshot_sha256"]
    assert ok["sections"][7]["items"][0]["kind"] == "FACT" and ok["sections"][7]["items"][0]["evidence"][0]["sha256"] == sha
    assert client.get(f"{API}/reports/{rep['id']}/pdf", headers=auth("op.ravi")).content.startswith(b"%PDF")
    assert client.post(f"{API}/reports/{rep['id']}/approve", json={"reason": "x", "review_evidence_ids": [sha]}, headers=auth("qm.farhan")).status_code == 409


def test_unknown_source_and_alert_source(client, auth, db):
    assert client.post(f"{API}/reports/8d", json={"source": {"type": "FOUR_M", "id": "nope"}}, headers=auth("qe.ananya")).json()["error"]["code"] == "SOURCE_NOT_FOUND"
    started(client, auth, db)
    alert = client.get(f"{API}/alerts", headers=auth("op.ravi")).json()["items"][0]
    rep = client.post(f"{API}/reports/8d", json={"source": {"type": "ALERT", "id": alert["id"]}}, headers=auth("qe.ananya")).json()
    assert rep["source_type"] == "ALERT" and client.get(f"{API}/reports/{rep['id']}/pdf", headers=auth("op.ravi")).status_code == 200
