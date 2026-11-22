import asyncio
from functools import partial
from threading import Event

import pytest
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.testclient import TestClient

from app.core.ws_bus import Bus, InvalidEvent

pytestmark = pytest.mark.nodb

AUDIENCES = {"fast": "plant-a", "slow": "plant-a", "other": "plant-b"}


def _event(event_id="evt1", **overrides):
    event = {
        "event_id": event_id,
        "type": "ALERT_CREATED",
        "severity": "CRITICAL",
        "audible_siren": True,
        "entity_type": "PACKAGE",
        "entity_id": "pkg1",
        "reason_codes": ["SEAL_SUSPICIOUS"],
        "timestamp": "2026-11-22T12:00:30.123Z",
        "payload": {"alert_id": "al1", "references": [{"id": "sc1"}]},
    }
    return {**event, **overrides}


def _make_app(bus, *, stalled=None, writer_finished=None):
    app = FastAPI()
    disconnected = {name: Event() for name in AUDIENCES}

    @app.websocket("/events/{name}")
    async def events(socket: WebSocket, name: str):
        await socket.accept()
        audience = AUDIENCES[name]
        await asyncio.gather(bus.subscribe(socket, audience=audience), bus.subscribe(socket, audience=audience))
        try:
            await socket.send_json({"subscribed": name})
            while True:
                await socket.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            await asyncio.gather(bus.unsubscribe(socket), bus.unsubscribe(socket))
            disconnected[name].set()

    async def asgi(scope, receive, send):
        async def send_with_backpressure(message):
            if stalled is not None and scope.get("path") == "/events/slow" and message["type"] == "websocket.send":
                if '"event_id"' in message.get("text", ""):
                    stalled.set()
                    try:
                        await asyncio.Future()
                    finally:
                        writer_finished.set()
            await send(message)

        await app(scope, receive, send_with_backpressure)

    return asgi, disconnected


def _publish(client, bus, event, audience="plant-a"):
    return client.portal.call(partial(bus.publish, event, audience=audience))


def test_subscribers_receive_only_their_audience_and_disconnect_cleanly():
    bus = Bus()
    app, disconnected = _make_app(bus)
    first, second = _event(), _event("evt2")
    with TestClient(app) as client:
        assert _publish(client, bus, first) == 0
        with client.websocket_connect("/events/fast") as fast, client.websocket_connect("/events/other") as other:
            assert fast.receive_json() == {"subscribed": "fast"}
            assert other.receive_json() == {"subscribed": "other"}
            assert _publish(client, bus, first) == 1
            assert _publish(client, bus, second, "plant-b") == 1
            assert fast.receive_json() == first
            assert other.receive_json() == second

            fast.close()
            assert disconnected["fast"].wait(2)
            assert _publish(client, bus, first) == 0
            assert _publish(client, bus, first, "plant-b") == 1
            assert other.receive_json() == first


def test_overflow_closes_only_the_slow_socket_and_cancels_its_inflight_send():
    bus = Bus(queue_size=1)
    stalled, writer_finished = Event(), Event()
    app, disconnected = _make_app(bus, stalled=stalled, writer_finished=writer_finished)
    with TestClient(app) as client:
        with client.websocket_connect("/events/fast") as fast, client.websocket_connect("/events/slow") as slow:
            assert fast.receive_json() == {"subscribed": "fast"}
            assert slow.receive_json() == {"subscribed": "slow"}
            assert _publish(client, bus, _event("sending")) == 2
            assert fast.receive_json()["event_id"] == "sending"
            assert stalled.wait(2)

            assert _publish(client, bus, _event("queued")) == 2
            assert fast.receive_json()["event_id"] == "queued"
            assert _publish(client, bus, _event("overflow")) == 1
            with pytest.raises(WebSocketDisconnect) as closed:
                slow.receive_json()
            assert closed.value.code == 1013
            assert writer_finished.is_set()
            assert fast.receive_json()["event_id"] == "overflow"

            slow.close()
            assert disconnected["slow"].wait(2)
            assert _publish(client, bus, _event("after-overflow")) == 1
            assert fast.receive_json()["event_id"] == "after-overflow"


def test_queued_data_is_independent_of_publisher_mutation():
    bus = Bus()
    app, _ = _make_app(bus)

    async def publish_then_mutate():
        event = _event()
        queued = await bus.publish(event, audience="plant-a")
        event["event_id"] = "changed"
        event["reason_codes"].append("CHANGED")
        event["payload"]["references"][0]["id"] = "changed"
        return queued

    with TestClient(app) as client, client.websocket_connect("/events/fast") as fast:
        assert fast.receive_json() == {"subscribed": "fast"}
        assert client.portal.call(publish_then_mutate) == 1
        assert fast.receive_json() == _event()


@pytest.mark.parametrize(
    "overrides",
    [
        {"type": "ALERT_ACKNOWLEDGED"},
        {"severity": "WARNING"},
        {"audible_siren": 1},
        {"reason_codes": "SEAL_SUSPICIOUS"},
        {"timestamp": "2026-02-30T12:00:30Z"},
        {"timestamp": "2026-11-22T12:00:30+00:00"},
        {"timestamp": "2026-11-22T12:00:30Z\n"},
        {"payload": []},
        {"payload": {"score": float("nan")}},
        {"payload": {"references": [{1: "non-string key"}]}},
        {"audience": "plant-b"},
    ],
)
def test_malformed_event_cannot_enter_any_queue(overrides):
    bus = Bus()
    app, _ = _make_app(bus)
    with TestClient(app) as client, client.websocket_connect("/events/fast") as fast:
        assert fast.receive_json() == {"subscribed": "fast"}
        with pytest.raises(InvalidEvent):
            _publish(client, bus, _event(**overrides), "unsubscribed")
        with pytest.raises(InvalidEvent):
            _publish(client, bus, _event(**overrides))
        sentinel = _event("after-invalid")
        assert _publish(client, bus, sentinel) == 1
        assert fast.receive_json() == sentinel


def test_required_claims_are_not_filled_in():
    bus = Bus()
    app, _ = _make_app(bus)
    with TestClient(app) as client:
        event = _event()
        del event["timestamp"]
        with pytest.raises(InvalidEvent):
            _publish(client, bus, event)
