import concurrent.futures as cf

import pytest
from sqlalchemy import select

from app.db import models as m
from app.db.locking import LockOrderViolation, lock_set


def test_health_reports_db_and_key(client):
    r = client.get("/api/v1/health")
    assert r.status_code == 200
    assert r.json() | {"version": None} == {"status": "ok", "db": "ok", "key_kid": "aq-test-1", "version": None, "demo": False}


def test_health_fails_closed_without_signing_key(client, monkeypatch, tmp_path):
    from app.core.config import settings
    monkeypatch.setattr(settings(), "autoq_signing_key_path", tmp_path / "missing.key")
    r = client.get("/api/v1/health")
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "SIGNING_KEY_UNAVAILABLE"
    assert r.json()["error"]["details"]["request_id"].startswith("req_")


def test_openapi_served(client):
    paths = client.get("/openapi.json").json()["paths"]
    assert {"/api/v1/health", "/api/v1/auth/login", "/api/v1/evidence/{sha256}", "/api/v1/audit/verify"} <= set(paths)


def test_unknown_route_uses_error_envelope(client):
    r = client.get("/api/v1/nope")
    assert r.status_code == 404 and r.json()["error"]["code"] == "NOT_FOUND"


def test_lock_set_refuses_out_of_order_and_after_audit(db):
    from app.core import audit
    ls = lock_set(db)
    ls.lock(m.Package, [])
    with pytest.raises(LockOrderViolation):
        ls.lock(m.Heat, ["x"])
    db.rollback()
    ls = lock_set(db)
    audit.append(db, "t", "X", "t", "1")
    with pytest.raises(LockOrderViolation):
        ls.lock(m.Unit, ["u"])
    db.rollback()


def test_audit_chain_is_serialized_under_concurrency():
    """20 concurrent transactions append to the chain; sequence stays gap-free and verifies."""
    from app.core import audit
    from app.db.session import session_factory

    def one(i):
        with session_factory()() as s:
            audit.append(s, "t", "CONCURRENT", "test", str(i), data={"i": i, "f": 0.5})
            s.commit()
    with cf.ThreadPoolExecutor(8) as ex:
        list(ex.map(one, range(20)))
    with session_factory()() as s:
        seqs = list(s.scalars(select(m.AuditEvent.seq).order_by(m.AuditEvent.seq)))
        assert seqs == list(range(1, len(seqs) + 1))
        assert audit.verify(s)["ok"] is True
