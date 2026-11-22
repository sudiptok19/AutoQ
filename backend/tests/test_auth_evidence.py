import hashlib

from sqlalchemy import text

from app.core.storage import get_evidence, put_evidence

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64


def test_login_me_and_bad_credentials(client, auth):
    r = client.get("/api/v1/auth/me", headers=auth("qm.farhan"))
    assert r.status_code == 200 and set(r.json()["roles"]) == {"quality_manager", "quality_engineer"}
    bad = client.post("/api/v1/auth/login", json={"username": "qm.farhan", "password": "nope"})
    assert bad.status_code == 401 and bad.json()["error"]["code"] == "INVALID_CREDENTIALS"
    unknown = client.post("/api/v1/auth/login", json={"username": "ghost", "password": "autoq-dev"})
    assert unknown.json()["error"]["code"] == "INVALID_CREDENTIALS"


def test_missing_or_garbage_token_is_401(client):
    assert client.get("/api/v1/auth/me").json()["error"]["code"] == "UNAUTHENTICATED"
    assert client.get("/api/v1/auth/me", headers={"Authorization": "Bearer x.y.z"}).status_code == 401


def test_role_removal_takes_effect_without_new_token(client, auth, db):
    h = auth("qm.farhan")
    assert client.get("/api/v1/audit/verify", headers=h).status_code == 200
    db.execute(text("DELETE FROM user_roles WHERE user_id='usr_farhan' AND role='quality_manager'")); db.commit()
    r = client.get("/api/v1/audit/verify", headers=h)
    assert r.status_code == 403 and r.json()["error"]["code"] == "ROLE_FORBIDDEN"


def test_dispatcher_forbidden_on_qm_endpoint(client, auth):
    r = client.get("/api/v1/audit/verify", headers=auth("disp.kiran"))
    assert r.status_code == 403 and r.json()["error"]["details"]["required"] == ["quality_manager", "admin"]


def test_evidence_roundtrip_dedup_and_rbac(client, auth, db):
    sha = put_evidence(db, PNG, "image/png", "LIVE_CAPTURE", "usr_ravi")
    assert put_evidence(db, PNG, "image/png", "LIVE_CAPTURE", "usr_ravi") == sha == hashlib.sha256(PNG).hexdigest()
    db.commit()
    assert client.get(f"/api/v1/evidence/{sha}").status_code == 401
    r = client.get(f"/api/v1/evidence/{sha}", headers=auth("disp.kiran"))
    assert r.status_code == 200 and r.content == PNG and r.headers["content-type"] == "image/png"
    assert client.get(f"/api/v1/evidence/{'0' * 64}", headers=auth("disp.kiran")).json()["error"]["code"] == "EVIDENCE_NOT_FOUND"
    assert client.get("/api/v1/evidence/not-a-digest", headers=auth("disp.kiran")).status_code == 422


def test_evidence_rejects_mislabelled_media_and_detects_corruption(db):
    import pytest

    from app.core.config import settings
    from app.core.errors import ApiError
    with pytest.raises(ApiError) as e:
        put_evidence(db, JPEG, "image/png", "LIVE_CAPTURE", None)
    assert e.value.code == "INVALID_MEDIA"
    sha = put_evidence(db, JPEG, "image/jpeg", "UPLOAD", None)
    p = settings().autoq_evidence_dir / sha[:2] / sha[2:4] / sha
    p.chmod(0o644); p.write_bytes(JPEG + b"tampered")
    with pytest.raises(ApiError) as e:
        get_evidence(db, sha)
    assert e.value.code == "EVIDENCE_CORRUPT"


def test_audit_list_and_tamper_detection(client, auth, db):
    h = auth("qm.farhan")  # login itself is audited
    items = client.get("/api/v1/audit", headers=h).json()["items"]
    assert items[0]["action"] == "LOGIN" and items[0]["actor"] == "qm.farhan"
    assert client.get("/api/v1/audit/verify", headers=h).json()["ok"] is True
    db.execute(text("UPDATE audit_events SET reason='edited' WHERE seq=1")); db.commit()
    v = client.get("/api/v1/audit/verify", headers=h).json()
    assert v == {"ok": False, "broken_at": 1, "length": 1}
