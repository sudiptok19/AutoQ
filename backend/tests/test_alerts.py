import pytest
from starlette.websockets import WebSocketDisconnect

from app.db.session import session_factory
from app.features.alerts.service import raise_alert

API = "/api/v1"


def make_alert(key="k1"):
    with session_factory()() as db:
        aid = raise_alert(db, type="SEAL_TAMPER", severity="CRITICAL", entity_type="PACKAGE", entity_id="pkg_x", reason_codes=["SEAL_SUSPICIOUS"],
                          details={"package": "PKG-1"}, dedupe_key=key, audible=True)
        db.commit()
        return aid


def test_alert_is_idempotent_and_listed(client, auth):
    a = make_alert()
    assert make_alert() is None  # same dedupe key → no second alert/event
    items = client.get(f"{API}/alerts", headers=auth("op.ravi")).json()["items"]
    assert [x["id"] for x in items] == [a] and items[0]["audible_siren"] is True


def test_ack_and_resolve_lifecycle(client, auth):
    a = make_alert()
    assert client.post(f"{API}/alerts/{a}/ack", json={"reason": "seen"}, headers=auth("op.ravi")).json()["status"] == "ACK"
    assert client.post(f"{API}/alerts/{a}/ack", json={"reason": "again"}, headers=auth("op.ravi")).status_code == 409
    assert client.post(f"{API}/alerts/{a}/resolve", json={"reason": "x"}, headers=auth("op.ravi")).status_code == 403
    r = client.post(f"{API}/alerts/{a}/resolve", json={"reason": "package re-sealed after QE review"}, headers=auth("qe.ananya")).json()
    assert r["status"] == "RESOLVED" and r["resolution"]["reason"].startswith("package")


def test_ws_requires_single_use_ticket(client, auth):
    with pytest.raises(WebSocketDisconnect) as e:
        with client.websocket_connect("/ws/events?ticket=nope"):
            pass
    assert e.value.code == 1008
    t = client.post(f"{API}/ws/tickets", headers=auth("disp.kiran")).json()["ticket"]
    with client.websocket_connect(f"/ws/events?ticket={t}"):
        pass
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(f"/ws/events?ticket={t}"):
            pass


def test_committed_alert_reaches_websocket(client, auth):
    t = client.post(f"{API}/ws/tickets", headers=auth("op.ravi")).json()["ticket"]
    with client.websocket_connect(f"/ws/events?ticket={t}") as ws:
        aid = make_alert("k-ws")
        ev = ws.receive_json()
        assert ev["type"] == "ALERT_CREATED" and ev["payload"]["alert_id"] == aid and ev["audible_siren"] is True
        assert ev["severity"] == "CRITICAL" and ev["timestamp"].endswith("Z")
